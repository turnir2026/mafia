# Спортивная мафия

Railway-ready Python site for a school tournament.

## Railway
- Start command comes from `Dockerfile`.
- Set `DATA_DIR=/app/data`.
- Attach a Railway Volume at `/app/data` so tournament data survives redeploys.
- Put player photos in `images/`: `1.png`, `2.png`, `3.png` and so on. The number follows the player's order in the admin list.

## Scoring
- Black team win: 4 points.
- Red team win: 3 points.
- Loss: 1 point.
- Fouls: 0–2 = 0 penalty; 3 = −0.5; 4 = −1.
- Extra points: 0 to 2 per game.
