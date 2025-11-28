# lively_marketplace_app.py
"""
Lively Marketplace (SQLite edition) - single-file Flask app
- Option A: SQLite, quick deploy.
- Start with: gunicorn lively_marketplace_app:app --workers=1 --threads=2
"""

from flask import Flask, request, session, redirect, url_for, render_template_string, flash, g
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from decimal import Decimal
from datetime import datetime, timezone
import os

APP_NAME = "Lively Marketplace"
HEADER_PHRASE = "Money = Survive"

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key")

# SQLite DB file in project root (Option A)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "lively_marketplace.db")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + DB_PATH
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# -------------------------
# Models
# -------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    balance_cents = db.Column(db.Integer, default=0)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    def set_password(self, p): self.password_hash = generate_password_hash(p)
    def check_password(self, p): return check_password_hash(self.password_hash, p)
    def credit(self, cents): self.balance_cents = (self.balance_cents or 0) + int(cents)
    def debit(self, cents): self.balance_cents = (self.balance_cents or 0) - int(cents)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(240), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price_cents = db.Column(db.Integer, nullable=False)
    image_url = db.Column(db.String(1000), nullable=True)
    seller_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    seller = db.relationship("User", backref="products")

class DebtListing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(240), nullable=False)
    principal_cents = db.Column(db.Integer, nullable=False)
    interest_rate_percent = db.Column(db.Float, nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    target_amount_cents = db.Column(db.Integer, default=0)
    total_invested_cents = db.Column(db.Integer, default=0)
    current_rate_percent = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    seller = db.relationship("User", backref="debt_listings")

class DebtPosition(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    debt_id = db.Column(db.Integer, db.ForeignKey("debt_listing.id"), nullable=False)
    principal_cents = db.Column(db.Integer, nullable=False)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    owner = db.relationship("User")
    debt = db.relationship("DebtListing")

# -------------------------
# Helpers
# -------------------------
def format_cents(cents):
    try:
        return f"${Decimal(cents)/100:.2f}"
    except Exception:
        return "$0.00"

@app.before_request
def load_user():
    g.user = None
    if "user_id" in session:
        g.user = User.query.get(session["user_id"])

# -------------------------
# Base HTML string (injected safely)
# -------------------------
BASE_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{app_name}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>body{{padding-top:1rem}}.card-img{{max-height:150px;object-fit:cover}}.small-muted{{font-size:0.9rem;color:#666}}</style>
</head><body>
<nav class="navbar navbar-expand-lg navbar-light bg-light mb-3"><div class="container-fluid">
  <a class="navbar-brand" href="/">{app_name}</a>
  <div class="collapse navbar-collapse">
    <ul class="navbar-nav me-auto">
      <li class="nav-item"><a class="nav-link" href="/products">Products</a></li>
      <li class="nav-item"><a class="nav-link" href="/debts">Debt Market</a></li>
      <li class="nav-item"><a class="nav-link" href="/investor">Investor</a></li>
    </ul>
    <span class="navbar-text me-3">{header}</span>
    {% if g.user %}
      <span class="me-2">Hi, {{ g.user.username }} — {{ format_cents(g.user.balance_cents) }}</span>
      <a class="btn btn-outline-secondary btn-sm me-2" href="/wallet">Wallet</a>
      {% if g.user.is_admin %}<a class="btn btn-warning btn-sm me-2" href="/admin">Admin</a>{% endif %}
      <a class="btn btn-outline-danger btn-sm" href="/logout">Logout</a>
    {% else %}
      <a class="btn btn-primary btn-sm me-2" href="/login">Login</a>
      <a class="btn btn-outline-primary btn-sm" href="/register">Register</a>
    {% endif %}
  </div></div></nav>
<div class="container">
{% with messages = get_flashed_messages() %}
  {% if messages %}<div class="alert alert-info">{{ messages[0] }}</div>{% endif %}
{% endwith %}
{% block body %}{% endblock %}
</div></body></html>""".format(app_name=APP_NAME, header=HEADER_PHRASE)

# -------------------------
# Routes (minimal, but functional)
# -------------------------
@app.route("/")
def index():
    products = Product.query.order_by(Product.created_at.desc()).limit(6).all()
    debts = DebtListing.query.order_by(DebtListing.created_at.desc()).limit(6).all()
    content = """
    <div class="row">
      <div class="col-md-6">
        <h3>Products</h3>
        {% for p in products %}
          <div class="card mb-2"><div class="row g-0"><div class="col-4">
            <img src="{{ p.image_url or 'https://via.placeholder.com/300x150' }}" class="img-fluid card-img">
          </div><div class="col-8"><div class="card-body">
            <h5 class="card-title">{{ p.title }}</h5>
            <p class="card-text small-muted">{{ (p.description or '')[:120] }}</p>
            <p class="card-text"><strong>{{ format_cents(p.price_cents) }}</strong></p>
            <a class="btn btn-sm btn-primary" href="/product/{{p.id}}">View</a>
          </div></div></div></div>
        {% endfor %}
      </div>
      <div class="col-md-6">
        <h3>Debt Market</h3>
        {% for d in debts %}
          <div class="mb-2"><a href="/debt/{{d.id}}">{{d.title}}</a> — {{ format_cents(d.principal_cents) }} ({{ '%.2f' % (d.current_rate_percent*100) }}%)</div>
        {% endfor %}
      </div>
    </div>
    """
    return render_template_string(BASE_HTML.replace("{% block body %}{% endblock %}", content), products=products, debts=debts, format_cents=format_cents)

@app.route("/init-db")
def init_db_route():
    db.create_all()
    return "DB initialized"

# Auto-create tables and a demo admin user
with app.app_context():
    try:
        db.create_all()
        if not User.query.filter_by(username="admin").first():
            admin = User(username="admin", is_admin=True)
            admin.set_password(os.getenv("ADMIN_PASS","adminpass"))
            admin.credit(100000)
            db.session.add(admin); db.session.commit()
            print("Created admin user 'admin'")
    except Exception as e:
        print("DB create_all error:", e)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)), debug=True)

