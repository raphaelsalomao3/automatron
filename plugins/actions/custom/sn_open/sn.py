#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import requests

payload = {
        "solicitante": sys.argv[1],
        "categoria": "0",
        "uop": "GSHOW",
        "sistemaEquipamento": "JB-1LC-VIAG-F3",
        "grupoDesignado": "SNOC",
        "descricaoResumida": "Gerado pelo framework de monitoração",
        "descricaoDetalhada": "Problema no " + sys.argv[2] + " - Automação gerada pelo Framework de Monitoração - Salomão SPLA",
        "prioridade": "4",
        "status": "1",
        "ativo": "55000015 - VF46HD - CDE-MG3",
        "ativoNaoRegistrado": "1111111",
        "modeloFabricanteNaoRegistrado": "Dell",
        "naoRegistrado": "XXXXXX",
        "indisponibilidade": "false"
        }

headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}

r = requests.post('https://servicesh.corp.tvglobo.com.br/Programacao/ServiceNow/ProxyService/IncluirIncidente_PS', json=payload, headers=headers, verify=False, auth=('integracao.sn', 'globo.10'))
print(r.url)
print(r.status_code)
print(r.text)
