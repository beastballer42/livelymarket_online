# lively_marketplace_app.py
"""
Single-file Lively Marketplace app — ready for Render.
Start with: gunicorn lively_marketplace_app:app --workers=1 --threads=2
"""

from flask import Flask, request, session, redirect, url_for, render_template_string, flash, g
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from decimal import Decimal
from datetime import datetime
import os, logging

# -----------------------
# App + config
# -----------------------
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")

# DATABASE: prefer Render's DATABASE_URL (Postgres). Otherwise use writable /tmp sqlite.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip() or None
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    # SQLAlchemy expects postgresql://
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not DATABASE_URL:
    # fallback to sqlite in /tmp (writable on Render)
    DATABASE_URL = "sqlite:////tmp/lively_marketplace.db"

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

print("📌 Using DATABASE:", app.config["SQLALCHEMY_DATABASE_URI"])

db = SQLAlchemy(app)

# Logger
logger = logging.getLogger("lively")
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())

# -----------------------
# Models
# -----------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    balance_cents = db.Column(db.Integer, default=0)  # stored as cents
    is_admin = db.Column(db.Boolean, default=False)
    stripe_account_id = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, p):
        self.password_hash = generate_password_hash(p)
    def check_password(self, p):
        return check_password_hash(self.password_hash, p)
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
    interest_rate_percent = db.Column(db.Float, nullable=False)  # e.g. 0.12 -> 12%
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
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    owner = db.relationship('User')
    debt = db.relationship('DebtListing')

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    commission_cents = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    buyer = db.relationship('User')
    product = db.relationship('Product')

# -----------------------
# Helpers
# -----------------------
def format_cents(cents):
    try:
        return f"${Decimal(cents)/100:.2f}"
    except Exception:
        return "$0.00"

PLATFORM_COMMISSION_PERCENT = Decimal(os.getenv("PLATFORM_COMMISSION_PERCENT", "5.0"))  # percent

def calculate_commission_cents(amount_cents):
    return int((Decimal(amount_cents) * PLATFORM_COMMISSION_PERCENT / 100).quantize(Decimal('1.')))

@app.before_request
def load_user():
    g.user = None
    if 'user_id' in session:
        try:
            g.user = User.query.get(session['user_id'])
        except Exception:
            g.user = None

# -----------------------
# Routes
# -----------------------
BASE_HTML = '''
<!doctype html>
<html>
<head>
 <meta charset="utf-8">
 <meta name="viewport" content="width=device-width,initial-scale=1">
 <title>Lively Marketplace</title>
 <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-light bg-light mb-3">
  <div class="container-fluid">
    <a class="navbar-brand" href="/">Lively Marketplace</a>
    <div class="collapse navbar-collapse">
      <ul class="navbar-nav me-auto">
        <li class="nav-item"><a class="nav-link" href="/products">Products</a></li>
        <li class="nav-item"><a class="nav-link" href="/debts">Debt Market</a></li>
      </ul>
      <span class="navbar-text me-3">Money = Survive</span>
      {% if g.user %}
        <span class="me-2">Hi, {{ g.user.username }} ({{ format_cents(g.user.balance_cents) }})</span>
        <a class="btn btn-outline-secondary btn-sm" href="/logout">Logout</a>
      {% else %}
        <a class="btn btn-primary btn-sm me-2" href="/login">Login</a>
        <a class="btn btn-outline-primary btn-sm" href="/register">Register</a>
      {% endif %}
    </div>
  </div>
</nav>
<div class="container">
  {% with messages = get_flashed_messages() %}
    {% if messages %}<div class="alert alert-info">{{ messages[0] }}</div>{% endif %}
  {% endwith %}
  {% block body %}{% endblock %}
</div>
</body>
</html>
'''

@app.route('/')
def index():
    products = Product.query.order_by(Product.created_at.desc()).limit(6).all()
    debts = DebtListing.query.order_by(DebtListing.created_at.desc()).limit(6).all()
    return render_template_string('{% extends base %}{% block body %}<h1>Featured</h1><div class="row"><div class="col-md-6"><h3>Products</h3>{% for p in products %}<div><a href="/product/{{p.id}}">{{p.title}}</a> — {{ format_cents(p.price_cents) }}</div>{% endfor %}</div><div class="col-md-6"><h3>Debts</h3>{% for d in debts %}<div><a href="/debt/{{d.id}}">{{d.title}}</a> — {{ format_cents(d.principal_cents) }} ({{ '%.2f' % (d.current_rate_percent*100) }}%)</div>{% endfor %}</div></div>{% endblock %}', base=BASE_HTML, products=products, debts=debts, format_cents=format_cents)

# Auth
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        if not username or not password:
            flash('Username & password required')
            return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash('Username taken')
            return redirect(url_for('register'))
        u = User(username=username)
        u.set_password(password)
        # Give small starting balance for demo
        u.credit(1000)  # $10
        db.session.add(u); db.session.commit()
        session['user_id'] = u.id
        flash('Registered')
        return redirect(url_for('index'))
    return render_template_string('{% extends base %}{% block body %}<h2>Register</h2><form method="post"><div class="mb-3"><input name="username" class="form-control" placeholder="Username"></div><div class="mb-3"><input name="password" type="password" class="form-control" placeholder="Password"></div><button class="btn btn-primary">Register</button></form>{% endblock %}', base=BASE_HTML)

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username','')
        password = request.form.get('password','')
        u = User.query.filter_by(username=username).first()
        if not u or not u.check_password(password):
            flash('Invalid credentials')
            return redirect(url_for('login'))
        session['user_id'] = u.id
        flash('Logged in')
        return redirect(url_for('index'))
    return render_template_string('{% extends base %}{% block body %}<h2>Login</h2><form method="post"><div class="mb-3"><input name="username" class="form-control" placeholder="Username"></div><div class="mb-3"><input name="password" type="password" class="form-control" placeholder="Password"></div><button class="btn btn-primary">Login</button></form>{% endblock %}', base=BASE_HTML)

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('Logged out')
    return redirect(url_for('index'))

# Products
@app.route('/products')
def products_list():
    items = Product.query.order_by(Product.created_at.desc()).all()
    return render_template_string('{% extends base %}{% block body %}<h2>Products</h2><a class="btn btn-success mb-3" href="/product/new">Sell Product</a><div>{% for p in items %}<div><a href="/product/{{p.id}}">{{ p.title }}</a> — {{ format_cents(p.price_cents) }}</div>{% endfor %}</div>{% endblock %}', base=BASE_HTML, items=items, format_cents=format_cents)

@app.route('/product/new', methods=['GET','POST'])
def product_new():
    if not g.user:
        flash('Login required')
        return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form.get('title','')
        desc = request.form.get('description','')
        price = Decimal(request.form.get('price','0'))
        p = Product(title=title, description=desc, price_cents=int(price*100), seller_id=g.user.id)
        db.session.add(p); db.session.commit()
        flash('Product listed')
        return redirect(url_for('products_list'))
    return render_template_string('{% extends base %}{% block body %}<h2>Sell Product</h2><form method="post"><div class="mb-3"><input class="form-control" name="title" placeholder="Title"></div><div class="mb-3"><textarea class="form-control" name="description" placeholder="Description"></textarea></div><div class="mb-3"><input class="form-control" name="price" placeholder="Price (e.g. 12.99)"></div><button class="btn btn-primary">List Product</button></form>{% endblock %}', base=BASE_HTML)

@app.route('/product/<int:product_id>')
def product_view(product_id):
    p = Product.query.get_or_404(product_id)
    return render_template_string('{% extends base %}{% block body %}<h2>{{p.title}}</h2><p>{{p.description}}</p><p><strong>{{ format_cents(p.price_cents) }}</strong></p>{% if g.user and g.user.id != p.seller_id %}<form method="post" action="/product/{{p.id}}/buy"><button class="btn btn-success">Buy</button></form>{% elif not g.user %}<a class="btn btn-primary" href="/login">Login to Buy</a>{% else %}<span class="text-muted">Your listing</span>{% endif %}{% endblock %}', base=BASE_HTML, p=p, format_cents=format_cents)

@app.route('/product/<int:product_id>/buy', methods=['POST'])
def product_buy(product_id):
    if not g.user:
        flash('Login required')
        return redirect(url_for('login'))
    p = Product.query.get_or_404(product_id)
    if p.seller_id == g.user.id:
        flash('Cannot buy your own product'); return redirect(url_for('product_view', product_id=product_id))
    amount = p.price_cents
    commission = calculate_commission_cents(amount)
    seller_receive = amount - commission
    if g.user.balance_cents < amount:
        flash('Insufficient balance. Top up via admin or integrate payments.'); return redirect(url_for('product_view', product_id=product_id))
    g.user.debit(amount)
    seller = User.query.get(p.seller_id)
    if seller:
        seller.credit(seller_receive)
    order = Order(buyer_id=g.user.id, product_id=p.id, amount_cents=amount, commission_cents=commission)
    db.session.add(order); db.session.commit()
    flash('Purchase complete')
    return redirect(url_for('products_list'))

# Debt marketplace
@app.route('/debts')
def debt_list():
    items = DebtListing.query.order_by(DebtListing.created_at.desc()).all()
    return render_template_string('{% extends base %}{% block body %}<h2>Debt Market</h2><a class="btn btn-success mb-3" href="/debt/new">List Debt</a><div>{% for d in items %}<div><a href="/debt/{{d.id}}">{{d.title}}</a> — {{ format_cents(d.principal_cents) }} ({{ "%.2f" % (d.current_rate_percent*100) }}%)</div>{% endfor %}</div>{% endblock %}', base=BASE_HTML, items=items, format_cents=format_cents)

@app.route('/debt/new', methods=['GET','POST'])
def debt_new():
    if not g.user:
        flash('Login required'); return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form.get('title','')
        principal = Decimal(request.form.get('principal','0'))
        rate = float(request.form.get('rate','0.1'))
        target = Decimal(request.form.get('target','0')) if request.form.get('target') else principal
        d = DebtListing(title=title, principal_cents=int(principal*100), interest_rate_percent=rate, seller_id=g.user.id)
        d.current_rate_percent = rate
        d.target_amount_cents = int(target*100)
        db.session.add(d); db.session.commit()
        flash('Debt listed'); return redirect(url_for('debt_list'))
    return render_template_string('{% extends base %}{% block body %}<h2>List Debt</h2><form method=post><div class="mb-3"><input class="form-control" name="title" placeholder="Title"></div><div class="mb-3"><input class="form-control" name="principal" placeholder="Principal (e.g. 1000.00)"></div><div class="mb-3"><input class="form-control" name="rate" placeholder="Interest rate as decimal (0.12 = 12%)"></div><div class="mb-3"><input class="form-control" name="target" placeholder="Target amount (optional)"></div><button class="btn btn-primary">List</button></form>{% endblock %}', base=BASE_HTML)

@app.route('/debt/<int:debt_id>')
def debt_view(debt_id):
    d = DebtListing.query.get_or_404(debt_id)
    return render_template_string('{% extends base %}{% block body %}<h2>{{d.title}}</h2><p>Principal: {{ format_cents(d.principal_cents) }}</p><p>Rate: {{ "%.2f" % (d.current_rate_percent*100) }}%</p>{% if g.user and g.user.id != d.seller_id %}<form method="post" action="/debt/{{d.id}}/buy">Amount:<input name="amount"><button class="btn btn-success">Buy</button></form>{% elif not g.user %}<a class="btn btn-primary" href="/login">Login to Buy</a>{% else %}<span class="text-muted">Your listing</span>{% endif %}{% endblock %}', base=BASE_HTML, d=d, format_cents=format_cents)

@app.route('/debt/<int:debt_id>/buy', methods=['POST'])
def debt_buy(debt_id):
    if not g.user:
        flash('Login required'); return redirect(url_for('login'))
    d = DebtListing.query.get_or_404(debt_id)
    amt = Decimal(request.form.get('amount','0'))
    cents = int(amt*100)
    if g.user.balance_cents < cents:
        flash('Insufficient balance'); return redirect(url_for('debt_view', debt_id=debt_id))
    # take funds from buyer, create position, credit seller minus commission
    g.user.debit(cents)
    pos = DebtPosition(owner_id=g.user.id, debt_id=d.id, principal_cents=cents)
    db.session.add(pos)
    commission = calculate_commission_cents(cents)
    seller_receive = cents - commission
    seller = User.query.get(d.seller_id)
    if seller:
        seller.credit(seller_receive)
    d.total_invested_cents = (d.total_invested_cents or 0) + cents

    # dynamic rate adjustment (aggressively volatile as requested)
    target = d.target_amount_cents or 1
    ratio = d.total_invested_cents / target
    if ratio < 0.01:
        d.current_rate_percent = min(1.0, d.current_rate_percent + 0.80)   # huge jump
    elif ratio < 0.05:
        d.current_rate_percent = min(1.0, d.current_rate_percent + 0.40)
    elif ratio < 0.2:
        d.current_rate_percent = min(1.0, d.current_rate_percent + 0.15)
    elif ratio < 0.5:
        d.current_rate_percent = max(0.0001, d.current_rate_percent + 0.05)
    elif ratio < 1.0:
        d.current_rate_percent = max(0.0001, d.current_rate_percent - 0.05)
    elif ratio < 1.5:
        d.current_rate_percent = max(0.0001, d.current_rate_percent - 0.25)
    else:
        d.current_rate_percent = max(0.0001, d.current_rate_percent - 0.50)

    db.session.commit()
    flash('Debt purchased')
    return redirect(url_for('debt_view', debt_id=debt_id))

# Admin quick top-up (dangerous: only for admin in prototype)
@app.route('/topup/<int:user_id>', methods=['POST'])
def topup(user_id):
    if not g.user or not g.user.is_admin:
        flash('Admin access required'); return redirect(url_for('index'))
    u = User.query.get_or_404(user_id)
    amount = Decimal(request.form.get('amount','0'))
    u.credit(int(amount*100))
    db.session.commit()
    flash('Topped up')
    return redirect(url_for('index'))

# Simple health check
@app.route('/healthz')
def healthz():
    return "ok"

# -----------------------
# Auto-create DB + admin user at import time (guaranteed)
# -----------------------
with app.app_context():
    try:
        db.create_all()
        # create default admin if missing
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', is_admin=True)
            admin.set_password(os.getenv("ADMIN_PASS","adminpass"))
            admin.credit(100000)  # $1000 for testing
            db.session.add(admin); db.session.commit()
            print("🔧 Created admin user 'admin'")
        print("🔥 DB ready")
    except Exception as e:
        print("❌ DB create_all() error:", e)

# Note: intentionally no app.run() — run under gunicorn: gunicorn lively_marketplace_app:app
