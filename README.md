# LionFlix Cortes

Lê uma lista M3U ou DNS (Xtream), separa filmes e séries, corta no tamanho escolhido
(30s, 60s, 3 min, 5 min ou 10 min) e gera a capa de cada corte automaticamente.

## Rodar no PC ou servidor
Requisitos: Python 3.8+ e ffmpeg no PATH.

    python3 server.py

Abra http://localhost:8000

## Rodar com Docker

    docker build -t lionflix-cortes .
    docker run -p 8000:8000 -v $(pwd)/cortes:/app/cortes lionflix-cortes

## Capas
Troque o `logo.png` pelo seu logo. Ele aparece no canto de cada capa com o texto "PARTE N".

## Modos de corte
- Rápido (padrão): sem recodificar, sem perda de qualidade. Pode variar alguns segundos.
- Preciso: recodifica, tempo exato, mais lento.

## Prévia visual
A pasta `docs/` tem uma prévia com dados de exemplo. Para publicar:
Settings > Pages > Branch `main` > pasta `/docs`.
O GitHub Pages só mostra a prévia. O corte de verdade precisa do servidor.

## Aviso
Não suba para o repositório a pasta `cortes/` nem listas com usuário e senha.
Use apenas conteúdo que você tem direito de usar e distribuir.
