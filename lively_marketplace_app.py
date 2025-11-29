"""
Lively Marketplace - Version D (single-file Flask app using local SQLite)
Run: python lively_marketplace_app.py
Requirements: flask, flask_sqlalchemy, werkzeug
This is a demo / preview. Do NOT use in production without security hardening.
Features:
- SQLite (local file lively_marketplace.db)
- User register/login (session cookie)
- Wallet top-up (simulated)
- Product marketplace (list, buy)
- Debt marketplace (list, invest)
- Dynamic interest rate adjustments on investments
- Admin user (created on first run: username=admin password=adminpass)
- Simple Bootstrap UI via render_template_string (no external templates)
"""

from flask import Flask, request, session, redirect, url_for, render_template_string, flash, g
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from decimal import Decimal
from datetime import datetime
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, 'lively_marketplace.db')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + DB_PATH
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    balance_cents = db.Column(db.Integer, default=0)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def credit(self, cents):
        self.balance_cents = (self.balance_cents or 0) + int(cents)

    def debit(self, cents):
        self.balance_cents = (self.balance_cents or 0) - int(cents)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price_cents = db.Column(db.Integer, nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    seller = db.relationship('User', backref='products')

class DebtListing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    principal_cents = db.Column(db.Integer, nullable=False)
    interest_rate_percent = db.Column(db.Float, nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    target_amount_cents = db.Column(db.Integer, default=0)
    total_invested_cents = db.Column(db.Integer, default=0)
    current_rate_percent = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    seller = db.relationship('User', backref='debt_listings')

class DebtPosition(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    debt_id = db.Column(db.Integer, db.ForeignKey('debt_listing.id'), nullable=False)
    principal_cents = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    owner = db.relationship('User')
    debt = db.relationship('DebtListing')

# --- Helpers ---
def format_cents(c):
    return f"${Decimal(c)/100:.2f}"

@app.before_request
def load_user():
    g.user = None
    if 'user_id' in session:
        g.user = User.query.get(session.get('user_id'))

# --- Templates (single-file) ---
BASE_HTML = '''<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lively Marketplace</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body{padding-top:1rem;background:#0b0f0b;color:#d6ffd8}
    .navbar, .card{background:#07110a}
    a, .nav-link{color:#9fe9b3 !important}
    .muted{color:#9aa49a}
  </style>
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-dark mb-3">
  <div class="container-fluid">
    <a class="navbar-brand" href="/">Lively Marketplace</a>
    <div class="collapse navbar-collapse">
      <ul class="navbar-nav me-auto mb-2 mb-lg-0">
        <li class="nav-item"><a class="nav-link" href="/products">Products</a></li>
        <li class="nav-item"><a class="nav-link" href="/debts">Debt Market</a></li>
      </ul>
      <span class="navbar-text me-3">Money = Survive</span>
      {% if g.user %}
        <span class="me-2">Hi, {{ g.user.username }} ({{ format_cents(g.user.balance_cents) }})</span>
        {% if g.user.is_admin %}<a class="btn btn-warning btn-sm me-2" href="/admin">Admin</a>{% endif %}
        <a class="btn btn-outline-danger btn-sm" href="/logout">Logout</a>
      {% else %}
        <a class="btn btn-primary btn-sm me-2" href="/login">Login</a>
        <a class="btn btn-outline-primary btn-sm" href="/register">Register</a>
      {% endif %}
    </div>
  </div>
</nav>
<div class="container">
  {% with messages = get_flashed_messages() %}{% if messages %}<div class="alert alert-info">{{ messages[0] }}</div>{% endif %}{% endwith %}
  {% block body %}{% endblock %}
</div>
</body>
</html>'''

# --- Routes ---
@app.route('/')
def index():
    products = Product.query.order_by(Product.created_at.desc()).limit(6).all()
    debts = DebtListing.query.order_by(DebtListing.created_at.desc()).limit(6).all()
    return render_template_string(
        "{% extends base %}{% block body %}<div class='row'><div class='col-md-8'><h3>Featured Products</h3><div class='row'>{% for p in products %}<div class='col-md-6'><div class='card mb-3 p-2'><h5>{{p.title}}</h5><p class='muted'>{{p.description or ''}}</p><p><strong>{{ format_cents(p.price_cents) }}</strong></p><a class='btn btn-sm btn-success' href='/product/{{p.id}}'>View</a></div></div>{% endfor %}</div></div><div class='col-md-4'><h3>Hot Debt Listings</h3>{% for d in debts %}<div class='card mb-2 p-2'><strong>{{d.title}}</strong><div class='muted'>Principal {{format_cents(d.principal_cents)}}</div><div>Rate: {{ '%.2f' % (d.current_rate_percent*100) }}%</div><a class='btn btn-sm btn-primary mt-2' href='/debt/{{d.id}}'>View</a></div>{% endfor %}</div></div>{% endblock %}",
        base=BASE_HTML, products=products, debts=debts, format_cents=format_cents
    )

# Auth
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        if not username or not password:
            flash('Missing username or password')
            return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash('Username taken')
            return redirect(url_for('register'))
        u = User(username=username)
        u.set_password(password)
        db.session.add(u); db.session.commit()
        session['user_id'] = u.id
        flash('Registered')
        return redirect(url_for('index'))
    return render_template_string('{% extends base %}{% block body %}<h2>Register</h2><form method=post><input class="form-control mb-2" name="username" placeholder="Username"><input class="form-control mb-2" name="password" placeholder="Password" type="password"><button class="btn btn-primary">Register</button></form>{% endblock %}', base=BASE_HTML)

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']; password = request.form['password']
        u = User.query.filter_by(username=username).first()
        if not u or not u.check_password(password):
            flash('Invalid credentials'); return redirect(url_for('login'))
        session['user_id'] = u.id; flash('Logged in'); return redirect(url_for('index'))
    return render_template_string('{% extends base %}{% block body %}<h2>Login</h2><form method=post><input class="form-control mb-2" name="username" placeholder="Username"><input class="form-control mb-2" name="password" placeholder="Password" type="password"><button class="btn btn-primary">Login</button></form>{% endblock %}', base=BASE_HTML)

@app.route('/logout')
def logout():
    session.pop('user_id', None); flash('Logged out'); return redirect(url_for('index'))

# Products
@app.route('/products')
def products_list():
    items = Product.query.order_by(Product.created_at.desc()).all()
    return render_template_string('{% extends base %}{% block body %}<h2>Products</h2><a class="btn btn-success mb-2" href="/product/new">Sell Product</a><div class="row">{% for p in items %}<div class="col-md-4"><div class="card mb-3 p-2"><h5>{{p.title}}</h5><p class="muted">{{p.description or ""}}</p><p><strong>{{format_cents(p.price_cents)}}</strong></p><a class="btn btn-sm btn-primary" href="/product/{{p.id}}">View</a></div></div>{% endfor %}</div>{% endblock %}', base=BASE_HTML, items=items, format_cents=format_cents)

@app.route('/product/new', methods=['GET','POST'])
def product_new():
    if not g.user: flash('Login required'); return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form['title']; desc = request.form.get('description',''); price = Decimal(request.form['price'])
        p = Product(title=title, description=desc, price_cents=int(price*100), seller_id=g.user.id)
        db.session.add(p); db.session.commit(); flash('Product listed'); return redirect(url_for('products_list'))
    return render_template_string('{% extends base %}{% block body %}<h2>Sell Product</h2><form method=post><input class="form-control mb-2" name="title" placeholder="Title"><textarea class="form-control mb-2" name="description" placeholder="Description"></textarea><input class="form-control mb-2" name="price" placeholder="Price (eg 12.99)"><button class="btn btn-primary">List</button></form>{% endblock %}', base=BASE_HTML)

@app.route('/product/<int:product_id>')
def product_view(product_id):
    p = Product.query.get_or_404(product_id)
    return render_template_string('{% extends base %}{% block body %}<h2>{{p.title}}</h2><p>{{p.description}}</p><p><strong>{{format_cents(p.price_cents)}}</strong></p>{% if g.user and g.user.id != p.seller_id %}<form method="post" action="/product/{{p.id}}/buy"><button class="btn btn-success">Buy</button></form>{% elif not g.user %}<a class="btn btn-primary" href="/login">Login to Buy</a>{% else %}<span class="muted">Your listing</span>{% endif %}{% endblock %}', base=BASE_HTML, p=p, format_cents=format_cents)

@app.route('/product/<int:product_id>/buy', methods=['POST'])
def product_buy(product_id):
    if not g.user: flash('Login required'); return redirect(url_for('login'))
    p = Product.query.get_or_404(product_id)
    if p.seller_id == g.user.id: flash('Cannot buy your own product'); return redirect(url_for('product_view', product_id=product_id))
    amount = p.price_cents; commission = int(amount * 0.05); seller_receive = amount - commission
    if g.user.balance_cents < amount: flash('Insufficient balance. Top up in Wallet.'); return redirect(url_for('product_view', product_id=product_id))
    g.user.debit(amount); seller = User.query.get(p.seller_id); seller.credit(seller_receive)
    db.session.commit(); flash('Purchase complete (simulated)'); return redirect(url_for('products_list'))

# Debt marketplace
@app.route('/debts')
def debt_list():
    items = DebtListing.query.order_by(DebtListing.created_at.desc()).all()
    return render_template_string('{% extends base %}{% block body %}<h2>Debt Market</h2><a class="btn btn-success mb-2" href="/debt/new">List Debt</a><div class="row">{% for d in items %}<div class="col-md-4"><div class="card mb-3 p-2"><h5>{{d.title}}</h5><p class="muted">Principal {{format_cents(d.principal_cents)}}</p><p>Rate: {{\"%.2f\" % (d.current_rate_percent*100)}}%</p><a class="btn btn-sm btn-primary" href="/debt/{{d.id}}">View</a></div></div>{% endfor %}</div>{% endblock %}', base=BASE_HTML, items=items, format_cents=format_cents)

@app.route('/debt/new', methods=['GET','POST'])
def debt_new():
    if not g.user: flash('Login required'); return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form['title']; principal = Decimal(request.form['principal']); rate = float(request.form['rate'])
        d = DebtListing(title=title, principal_cents=int(principal*100), interest_rate_percent=rate, seller_id=g.user.id)
        d.current_rate_percent = rate; d.target_amount_cents = int(request.form.get('target', int(principal*100)))
        db.session.add(d); db.session.commit(); flash('Debt listed'); return redirect(url_for('debt_list'))
    return render_template_string('{% extends base %}{% block body %}<h2>List Debt</h2><form method=post><input class="form-control mb-2" name="title" placeholder="Title"><input class="form-control mb-2" name="principal" placeholder="Principal (e.g. 1000.00)"><input class="form-control mb-2" name="rate" placeholder="Interest rate percent (e.g. 0.8 for 80%)"><input class="form-control mb-2" name="target" placeholder="Target (optional)"><button class="btn btn-primary">List</button></form>{% endblock %}', base=BASE_HTML)

@app.route('/debt/<int:debt_id>')
def debt_view(debt_id):
    d = DebtListing.query.get_or_404(debt_id)
    return render_template_string('{% extends base %}{% block body %}<h2>{{d.title}}</h2><p>Principal: {{format_cents(d.principal_cents)}}</p><p>Rate: {{"%.2f" % (d.current_rate_percent*100)}}%</p>{% if g.user and g.user.id != d.seller_id %}<form method="post" action="/debt/{{d.id}}/buy">Amount:<input class="form-control mb-2" name="amount" placeholder="Amount (e.g. 100)"><button class="btn btn-success">Buy Debt</button></form>{% elif not g.user %}<a class="btn btn-primary" href="/login">Login to Buy</a>{% else %}<span class="muted">Your listing</span>{% endif %}{% endblock %}', base=BASE_HTML, d=d, format_cents=format_cents)

@app.route('/debt/<int:debt_id>/buy', methods=['POST'])
def debt_buy(debt_id):
    if not g.user: flash('Login required'); return redirect(url_for('login'))
    d = DebtListing.query.get_or_404(debt_id)
    if d.seller_id == g.user.id: flash('Cannot buy your own debt'); return redirect(url_for('debt_view', debt_id=debt_id))
    amt = Decimal(request.form.get('amount','0')); cents = int(amt*100)
    if g.user.balance_cents < cents: flash('Insufficient balance'); return redirect(url_for('debt_view', debt_id=debt_id))
    g.user.debit(cents); pos = DebtPosition(owner_id=g.user.id, debt_id=d.id, principal_cents=cents); db.session.add(pos)
    d.total_invested_cents = (d.total_invested_cents or 0) + cents
    # rate update algorithm (aggressive possibilities)
    target = d.target_amount_cents or 1
    ratio = d.total_invested_cents / target
    if ratio < 0.01: d.current_rate_percent = min(0.95, d.current_rate_percent + 0.20)
    elif ratio < 0.05: d.current_rate_percent = min(0.95, d.current_rate_percent + 0.12)
    elif ratio < 0.2: d.current_rate_percent = min(0.95, d.current_rate_percent + 0.06)
    elif ratio < 0.5: d.current_rate_percent = max(0.001, d.current_rate_percent + 0.02)
    elif ratio < 1.0: d.current_rate_percent = max(0.001, d.current_rate_percent - 0.03)
    elif ratio < 1.5: d.current_rate_percent = max(0.001, d.current_rate_percent - 0.12)
    else: d.current_rate_percent = max(0.0001, d.current_rate_percent - 0.30)
    db.session.commit(); flash('Debt purchased (simulated)'); return redirect(url_for('debt_view', debt_id=debt_id))

# Wallet topup (admin or user)
@app.route('/wallet', methods=['GET','POST'])
def wallet():
    if not g.user: flash('Login required'); return redirect(url_for('login'))
    if request.method == 'POST':
        amt = Decimal(request.form.get('amount','0')); cents = int(amt*100)
        g.user.credit(cents); db.session.commit(); flash('Wallet topped up (simulated)'); return redirect(url_for('wallet'))
    return render_template_string('{% extends base %}{% block body %}<h2>Wallet</h2><p class="muted">Balance: {{format_cents(g.user.balance_cents)}}</p><form method=post><input class="form-control mb-2" name="amount" placeholder="Amount to top up (e.g. 100)"><button class="btn btn-primary">Top Up</button></form>{% endblock %}', base=BASE_HTML, format_cents=format_cents)

# Admin panel
@app.route('/admin')
def admin_panel():
    if not g.user or not g.user.is_admin: flash('Admin required'); return redirect(url_for('index'))
    users = User.query.order_by(User.created_at.desc()).all()
    total_commission = 0
    return render_template_string('{% extends base %}{% block body %}<h2>Admin</h2><h4>Users</h4><table class="table"><thead><tr><th>User</th><th>Balance</th><th>Joined</th></tr></thead><tbody>{% for u in users %}<tr><td>{{u.username}}</td><td>{{format_cents(u.balance_cents)}}</td><td>{{u.created_at.strftime("%Y-%m-%d")}}</td></tr>{% endfor %}</tbody></table>{% endblock %}', base=BASE_HTML, users=users, format_cents=format_cents)

# CLI command to init DB and create admin
@app.cli.command('initdb')
def initdb_command():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        u = User(username='admin', is_admin=True); u.set_password('adminpass'); u.credit(100000); db.session.add(u); db.session.commit()
    print('Initialized the database and created admin user.')

# Auto-init DB on startup
with app.app_context():
    try:
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            u = User(username='admin', is_admin=True); u.set_password('adminpass'); u.credit(100000); db.session.add(u); db.session.commit()
        print('DB OK')
    except Exception as e:
        print('DB init error', e)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)


