# Protocolo para medir o processo manual

Objetivo: ter uma linha de base honesta para comparar com o tempo do agente. Não usamos estimativas.

1. Gere a pasta: `python demo/generate_data.py` (17 documentos em `demo/data/inbox`).
2. Inicie um cronômetro.
3. Sem usar IA, produza:
   - uma planilha com número, fornecedor, CNPJ, emissão, vencimento e total de cada fatura;
   - o total por fornecedor e por categoria;
   - a lista do que vence até 03/11/2026 (referência: 20/10/2026) e do que já venceu;
   - a identificação de cobranças duplicadas e de totais que não batem com a soma dos itens;
   - um resumo de meia página.
4. Pare o cronômetro e anote o tempo e quantos dos 2 problemas plantados você encontrou.
5. Registre em `bench/results/manual_<seu-nome>.md`: tempo total, problemas encontrados e erros de digitação percebidos depois.

Compare com `segundos` e `problemas_detectados` do benchmark automatizado.
