Lively Marketplace - compact release.
Files: lively_marketplace_app.py, requirements.txt, Dockerfile, docker-compose.yml
Run locally:
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY=yourkey
flask --app lively_marketplace_app.py initdb  # or run the app to auto-create DB
flask --app lively_marketplace_app.py run
