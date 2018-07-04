'''

Automatron: Actioning

  * Subscribe to Health Check Status queue
  * Execute (in a thread) applicable runbooks

'''


import sys
import signal
import json
import tempfile
import shutil
import fabric.api
import os
from sets import Set
import time
import core.common
import core.logs
import core.db
import core.fab

def listen(config, dbc, logger):
    ''' Listen for new events and schedule runbooks '''
    logger.info("Starting subscription to monitors channel")
    pubsub = dbc.subscribe("check:results")
    item = None
    target = None
    for msg in pubsub.listen():
        logger.debug("Got message: {0}".format(msg))
        try:
            item = dbc.process_subscription(msg)
            logger.debug("Received {0} notification for {1}".format(
                item['msg_type'], item['target']))
            target = dbc.get_target(target_id=item['target'])
            logger.debug("Found target: {0}".format(json.dumps(target)))
        except Exception as e:
            logger.warn("Unable to process message: {0}".format(e.message))
        if item and target:
            target = update_target_status(item, target)
            dbc.save_target(target=target)
            log_string = "{0} {1} returned:".format(item['runbook'], target['hostname'])
            for status in target['runbooks'][item['runbook']]['status'].keys():
                log_string = log_string + " {0} {1}".format(
                    target['runbooks'][item['runbook']]['status'][status], status)
            logger.info(log_string)
            runbooks = get_runbooks_to_exec(item, target, logger)
            logger.debug("Identified {0} runbook with actions".format(len(runbooks)))
            for runbook in runbooks:
                for action in runbooks[runbook]:
                    logger.info("Executing action {0} from runbook {1} on target {2}".format(
                        action, runbook, target['hostname']))
                    if execute_runbook(target['runbooks'][runbook]['actions'][action],
                                       target, config, logger):
                        logger.debug("Execution of action {0} on target {1} Successful".format(
                            action, target['hostname']))
                        target['runbooks'][runbook]['actions'][action]['last_run'] = time.time()
                        dbc.save_target(target=target)
                    else:
                        logger.debug("Execution of action {0} on target {1} Failed".format(
                            action, target['hostname']))

def update_target_status(item, target):
    ''' Update the target:runbook status counters '''
    not_found = Set(['OK', 'WARNING', 'CRITICAL', 'UNKNOWN'])
    if "status" not in target['runbooks'][item['runbook']]:
        target['runbooks'][item['runbook']]['status'] = {
            'OK' : 0,
            'WARNING' : 0,
            'CRITICAL' : 0,
            'UNKNOWN' : 0
        }
        target['runbooks'][item['runbook']]['last_status'] = None
    # Increase counts of status'
    for check in item['checks'].keys():
        # Update Status Counter
        target['runbooks'][item['runbook']]['status'][item['checks'][check]] = (
            target['runbooks'][item['runbook']]['status'][item['checks'][check]] + 1)
        # Update last_status with return
        target['runbooks'][item['runbook']]['last_status'] = item['checks'][check]
        # Removes status from list of not found status'
        not_found.discard(item['checks'][check])

    # Reset count to 0 for missing status'
    for missing_status in not_found:
        target['runbooks'][item['runbook']]['status'][missing_status] = 0

    return target

def get_runbooks_to_exec(item, target, logger):
    ''' Determine which Runbooks to execute based on message and target '''
    run_these = {item['runbook'] : Set([])}
    # Cycle through logic checks
    for action in target['runbooks'][item['runbook']]['actions'].keys():
        # Start false
        run_me = False
        call_on = target['runbooks'][item['runbook']]['actions'][action]['call_on']
        trigger = target['runbooks'][item['runbook']]['actions'][action]['trigger']
        frequency = target['runbooks'][item['runbook']]['actions'][action]['frequency']
	
	if "run_once" in target['runbooks'][item['runbook']]['actions'][action]:
	    run_once = target['runbooks'][item['runbook']]['actions'][action]['run_once']

        last_run = 0
        if "last_run" in target['runbooks'][item['runbook']]['actions'][action]:
            last_run = target['runbooks'][item['runbook']]['actions'][action]['last_run']


        # see if we are beyond or equal to trigger threshold
        for status in call_on:
            if trigger <= target['runbooks'][item['runbook']]['status'][status]:
                logger.debug("Action {0} has trigger of {1} and status has {2}".format(
                    action, trigger,
                    target['runbooks'][item['runbook']]['status'][status]))
                run_me = True # turn True
            else:
                logger.debug("Action {0} has trigger of {1} and status has {2}".format(
                    action, trigger,
                    target['runbooks'][item['runbook']]['status'][status]))
        # see if we recently ran this action
        if frequency > (time.time() - last_run):
            run_me = False # set to false

        for check in item['checks'].keys():
            if item['checks'][check] not in call_on:
                run_me = False
        
	# Checking if run_once action has already run
	# logger.debug("Action {}: Last run {} and Run Once {}".format(action, last_run, run_once))    
	if last_run != 0 and run_once:
	    logger.debug("Action {} was already run once and will not run again".format(action))    
	    run_me = False #turns False as action has already run

        if run_me is True:
            run_these[item['runbook']].add(action)
    return run_these

def execute_runbook(action, target, config, logger):
    ''' Execute action against target '''
    # Setup default fabric environment
    fabric.api.env = core.fab.set_env(config, fabric.api.env)
    # Check need for override
    if "credentials" in action:
        fabric.api.env = core.fab.set_env(config, fabric.api.env, override=action['credentials'])

    results = None

    # Start plugin based action
    if "plugin" in action['type']:
        plugin_file = action['plugin']
        plugin_file = '{0}/actions/{1}'.format(config['plugin_path'], plugin_file)
        dest_name = next(tempfile._get_candidate_names())
        destination = "{0}/{1}".format(config['actioning']['upload_path'], dest_name)
        with fabric.api.hide('output', 'running', 'warnings'):
            try:
                if "target" in action['execute_from'] or "host" in action['execute_from']:
                    if "target" in action['execute_from']:
                        fabric.api.env.host_string = target['ip']
                    else:
                        fabric.api.env.host_string = action['host']
                    logger.debug("Placing plugin script into {0} on {1}".format(destination, fabric.api.env.host_string))
                    fabric.api.put(plugin_file, destination)
                    fabric.api.run("chmod 700 {0}".format(destination))
                    cmd = "{0} {1}".format(destination, action['args'])
                    results = fabric.api.run(cmd)
                    fabric.api.run("rm {0}".format(destination))
                elif "remote" in action['execute_from']:
                    # Move file to temporary location and execute
                    shutil.copyfile(plugin_file, "/tmp/{0}".format(dest_name))
                    os.chmod("/tmp/{0}".format(dest_name), 0700)
                    cmd = "/tmp/{0} {1}".format(dest_name, action['args'])
                    results = fabric.api.local(cmd, capture=True)
                    os.remove("/tmp/{0}".format(dest_name))
                else:
                    logger.warn('Unknown "execute_from" specified in action')
                    return False
            except Exception as e:
                logger.debug("Could not execute plugin {0} for target {1}: {2}".format(
                    plugin_file, target['ip'], e.message))

    # Start command based action
    else:
        cmd = action['cmd']
        # Perform Check
        with fabric.api.hide('output', 'running', 'warnings'):
            try:
                if "target" in action['execute_from'] or "host" in action['execute_from']:
                    if "target" in action['execute_from']:
                        fabric.api.env.host_string = target['ip']
                    else:
                        fabric.api.env.host_string = action['host']
                    results = fabric.api.run(cmd)
                elif "remote" in action['execute_from']:
                    results = fabric.api.local(cmd, capture=True)
                else:
                    logger.warn('Unknown "execute_from" specified in action')
                    return False
            except Exception as e:
                logger.debug("Could not execute command {0}".format(cmd))

    # Check results
    if results:
        if results.succeeded is True:
            return True
        else:
            return False
    else:
        return False

def shutdown(signum, frame):
    ''' Shutdown this process '''
    dbc.disconnect()
    if signum == 15 or signum == 2:
        logger.info("Received signal {0} shutting down".format(signum))
        sys.exit(0)
    elif signum == 0:
        sys.exit(1)
    else:
        logger.error("Received signal {0} shutting down".format(signum))
        sys.exit(1)

if __name__ == "__main__":
    config = core.common.get_config(description="Automatron: Actioning")
    if config is False:
        print "Could not get configuration"
        sys.exit(1)

    # Setup Logging
    logs = core.logs.Logger(config=config, proc_name="actioning")
    logger = logs.getLogger()

    # Listen for signals
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Open Datastore Connection
    db = core.db.SetupDatastore(config=config)
    try:
        dbc = db.get_dbc()
    except Exception as e:
        logger.error("Failed to connect to datastore: {0}".format(e.message))
        shutdown(0, None)

    while True:
        listen(config, dbc, logger)
