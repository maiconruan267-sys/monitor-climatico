# Monitor Climático Sul

Migração do monitor existente, preservando as visões executiva, regional e analítica, os mapas diários, filtros, tabelas e extremos previstos.

## Publicação

Em Settings → Pages → Build and deployment, selecione **GitHub Actions** como Source. O workflow `Atualizar clima e publicar` consulta o clima, salva a base no Git e publica o site. Pode ser executado manualmente em Actions → Run workflow.

Endereço previsto após a publicação: https://maiconruan267-sys.github.io/monitor-climatico/

## Atualização e histórico

- Atualização diária programada para 06h17 em Brasília (09h17 UTC), sujeita à fila do GitHub Actions.
- Consulta das mesmas cidades de `_sistema/Locais.csv`, com a mesma fonte e variáveis do sistema original.
- A base acumulada permanece em `_sistema/dados_clima.js`; cada coleta bem-sucedida salva também `historico/AAAA-MM-DD.json`. Reexecuções no mesmo dia atualizam esse arquivo, e o Git preserva as versões anteriores.
- Datas antigas são mantidas; dados da janela recente são substituídos pelos mais novos. Uma cidade com falha mantém sua série anterior. Com menos de 95% de sucesso, a execução falha sem substituir a base publicada.
- Os arquivos importados inicialmente são de setembro de 2026. A primeira atualização precisa concluir para publicar dados atuais. Não são reconstruídos automaticamente dias ausentes entre essa base e a janela recente da API.
- O navegador carrega a base publicada; o botão de atualização recarrega essa base, sem iniciar uma coleta no servidor.
- Em repositórios públicos, o GitHub pode suspender agendamentos após 60 dias sem atividade; acompanhe a aba Actions caso o indicador de atualização fique antigo.

## Fontes

Clima: [Open-Meteo](https://open-meteo.com/), dados modelados de histórico recente e previsão, não medições oficiais locais. Malhas: [IBGE](https://servicodados.ibge.gov.br/api/docs/malhas?versao=3). Os arquivos de malhas originais foram preservados.

## Verificação local

Execute `python -m unittest discover -s tests` para verificar preservação de histórico e rejeição de respostas incompletas. Sirva a raiz com `python -m http.server 8000` para abrir a interface.
