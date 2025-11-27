# lively_marketplace_app.py
"""
Lively Marketplace — single-file app with:
- Stripe Checkout (top-ups) + webhook
- Product marketplace (images via URL)
- Investor debt marketplace with dynamic rates
- Investor dashboard (ROI calc) and admin payouts
- Auto DB creation, Render-ready (Postgres preferred, fallback to /tmp sqlite)
Run with:
    gunicorn lively_marketplace_app:app --workers=1 --threads=2
Environment variables:
    SECRET_KEY
    DATABASE_URL (optional)
    STRIPE_API_KEY (optional, test/live)
    STRIPE_WEBHOOK_SECRET (optional)
    ADMIN_PASS (optional)
    PLATFORM_COMMISSION_PERCENT (optional, default 5.0)
"""

import os, logging, json, math
from decimal import Decimal
from datetime import datetime, timezone
from flask import Flask, request, session, redirect, url_for, render_template_string, flash, g, jsonify, abort

# try import stripe (won't fail deploy if not installed — but include it in requirements)
try:
    import stripe
except Exception:
    stripe = None

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# -----------------------
# App config
# -----------------------
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")

# DATABASE: prefer DATABASE_URL (Postgres). Fallback to SQLite in /tmp for Render.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip() or None
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if not DATABASE_URL:
    DATABASE_URL = "sqlite:////tmp/lively_marketplace.db"
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

print("📌 Using DATABASE:", app.config["SQLALCHEMY_DATABASE_URI"])

db = SQLAlchemy(app)

# Stripe setup (optional)
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
if stripe and STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY

# Commission
PLATFORM_COMMISSION_PERCENT = Decimal(os.getenv("PLATFORM_COMMISSION_PERCENT", "5.0"))

# Logging
logger = logging.getLogger("lively")
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

# -----------------------
# Models
# -----------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    balance_cents = db.Column(db.Integer, default=0)
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
    interest_rate_percent = db.Column(db.Float, nullable=False)  # e.g., 0.12 = 12%
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

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    commission_cents = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    buyer = db.relationship("User")
    product = db.relationship("Product")

class PayoutRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    paid = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship("User")

# -----------------------
# Helpers
# -----------------------
def format_cents(cents):
    try:
        return f"${Decimal(cents)/100:.2f}"
    except Exception:
        return "$0.00"

def calculate_commission_cents(amount_cents):
    return int((Decimal(amount_cents) * PLATFORM_COMMISSION_PERCENT / 100).quantize(Decimal('1.')))

@app.before_request
def load_user():
    g.user = None
    if "user_id" in session:
        g.user = User.query.get(session["user_id"])

# -----------------------
# Simple base template (Bootstrap)
# -----------------------
BASE_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Lively Marketplace</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body{padding-top:1rem}
    .card-img{max-height:150px;object-fit:cover;}
    .small-muted{font-size:0.9rem;color:#666}
  </style>
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-light bg-light mb-3">
  <div class="container-fluid">
    <a class="navbar-brand" href="/">Lively Marketplace</a>
    <div class="collapse navbar-collapse">
      <ul class="navbar-nav me-auto">
        <li class="nav-item"><a class="nav-link" href="/products">Products</a></li>
        <li class="nav-item"><a class="nav-link" href="/debts">Debt Market</a></li>
        <li class="nav-item"><a class="nav-link" href="/investor">Investor Dashboard</a></li>
      </ul>
      <span class="navbar-text me-3">Money = Survive</span>
      {% if g.user %}
        <span class="me-2">Hi, {{ g.user.username }} — {{ format_cents(g.user.balance_cents) }}</span>
        <a class="btn btn-outline-secondary btn-sm me-2" href="/wallet">Wallet</a>
        {% if g.user.is_admin %}
          <a class="btn btn-warning btn-sm me-2" href="/admin">Admin</a>
        {% endif %}
        <a class="btn btn-outline-danger btn-sm" href="/logout">Logout</a>
      {% else %}
        <a class="btn btn-primary btn-sm me-2" href="/login">Login</a>
        <a class="btn btn-outline-primary btn-sm" href="/register">Register</a>
      {% endif %}
    </div>
  </div>
</nav>
<div class="container">
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <div class="alert alert-info">{{ messages[0] }}</div>
    {% endif %}
  {% endwith %}
  {% block body %}{% endblock %}
</div>
</body>
</html>
"""

# -----------------------
# Routes — Home
# -----------------------
@app.route("/")
def index():
    products = Product.query.order_by(Product.created_at.desc()).limit(6).all()
    debts = DebtListing.query.order_by(DebtListing.created_at.desc()).limit(6).all()
    return render_template_string(
        "{% extends base %}{% block body %}"
        "<div class='row'><div class='col-md-6'><h3>Products</h3>"
        "{% for p in products %}"
        "<div class='card mb-2'><div class='row g-0'><div class='col-4'><img src='{{ p.image_url or \"https://via.placeholder.com/300x150\" }}' class='img-fluid card-img'></div><div class='col-8'><div class='card-body'><h5 class='card-title'>{{p.title}}</h5><p class='card-text small-muted'>{{p.description[:120]}}</p><p class='card-text'><strong>{{ format_cents(p.price_cents) }}</strong></p><a class='btn btn-sm btn-primary' href='/product/{{p.id}}'>View</a></div></div></div></div>"
        "{% endfor %}</div>"
        "<div class='col-md-6'><h3>Debts</h3>"
        "{% for d in debts %}"
        "<div class='mb-2'><a href='/debt/{{d.id}}'>{{d.title}}</a> — {{ format_cents(d.principal_cents) }} ({{ '%.2f' % (d.current_rate_percent*100) }}%)</div>"
        "{% endfor %}</div></div>"
        "{% endblock %}",
        base=BASE_HTML, products=products, debts=debts, format_cents=format_cents
    )

# -----------------------
# Auth
# -----------------------
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","")
        if not username or not password:
            flash("username & password required"); return redirect(url_for("register"))
        if User.query.filter_by(username=username).first():
            flash("username taken"); return redirect(url_for("register"))
        u = User(username=username); u.set_password(password); u.credit(1000)  # $10 start for demo
        db.session.add(u); db.session.commit()
        session["user_id"] = u.id; flash("registered"); return redirect(url_for("index"))
    return render_template_string("{% extends base %}{% block body %}<h2>Register</h2><form method=post><div class='mb-3'><input name='username' class='form-control' placeholder='Username'></div><div class='mb-3'><input name='password' type='password' class='form-control' placeholder='Password'></div><button class='btn btn-primary'>Register</button></form>{% endblock %}", base=BASE_HTML)

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username",""); password = request.form.get("password","")
        u = User.query.filter_by(username=username).first()
        if not u or not u.check_password(password):
            flash("invalid credentials"); return redirect(url_for("login"))
        session["user_id"] = u.id; flash("logged in"); return redirect(url_for("index"))
    return render_template_string("{% extends base %}{% block body %}<h2>Login</h2><form method=post><div class='mb-3'><input name='username' class='form-control' placeholder='Username'></div><div class='mb-3'><input name='password' type='password' class='form-control' placeholder='Password'></div><button class='btn btn-primary'>Login</button></form>{% endblock %}", base=BASE_HTML)

@app.route("/logout")
def logout():
    session.pop("user_id", None); flash("logged out"); return redirect(url_for("index"))

# -----------------------
# Wallet & Stripe Top-up
# -----------------------
@app.route("/wallet")
def wallet():
    if not g.user:
        return redirect(url_for("login"))
    return render_template_string("{% extends base %}{% block body %}<h2>Wallet</h2><p>Balance: {{ format_cents(g.user.balance_cents) }}</p><a class='btn btn-success' href='/pay/topup'>Top up via Stripe</a><h4 class='mt-3'>Request Payout</h4><form method=post action='/request-payout'><div class='mb-3'><input name='amount' placeholder='Amount (e.g. 10.00)' class='form-control'></div><button class='btn btn-warning'>Request Payout</button></form>{% endblock %}", base=BASE_HTML, format_cents=format_cents)

@app.route("/pay/topup", methods=["GET"])
def pay_topup():
    if not g.user:
        return redirect(url_for("login"))
    # simple top-up UI: choose amount, then create checkout session
    return render_template_string("{% extends base %}{% block body %}<h2>Top up</h2><form method=post action='/stripe/create-checkout-session'><div class='mb-3'><input name='amount' placeholder='Amount (e.g. 10.00)' class='form-control'></div><button class='btn btn-primary'>Pay</button></form>{% if stripe_enabled %}<p class='small-muted mt-2'>Payments processed by Stripe.</p>{% else %}<p class='text-muted mt-2'>Stripe not configured on server — this will simulate a top-up.</p>{% endif %}{% endblock %}", base=BASE_HTML, stripe_enabled=bool(stripe and STRIPE_API_KEY))

@app.route("/stripe/create-checkout-session", methods=["POST"])
def stripe_create_checkout():
    if not g.user:
        return redirect(url_for("login"))
    amt = request.form.get("amount","0").strip()
    try:
        amount = Decimal(amt)
        if amount <= 0:
            raise ValueError()
    except Exception:
        flash("Invalid amount"); return redirect(url_for("pay_topup"))
    cents = int(amount * 100)
    # If stripe configured, create real Checkout session and set client_reference_id to user id.
    if stripe and STRIPE_API_KEY:
        domain = request.url_root[:-1]
        try:
            session_obj = stripe.checkout.Session.create(
                payment_method_types=["card"],
                mode="payment",
                line_items=[{"price_data":{"currency":"usd","product_data":{"name":"Lively Top-up"},"unit_amount":cents},"quantity":1}],
                success_url=domain + url_for("stripe_success") + "?session_id={CHECKOUT_SESSION_ID}",
                cancel_url=domain + url_for("pay_topup"),
                client_reference_id=str(g.user.id),
            )
            return redirect(session_obj.url, code=303)
        except Exception as e:
            logger.exception("Stripe session error")
            flash("Stripe error: " + str(e)); return redirect(url_for("pay_topup"))
    else:
        # Simulate: directly credit user (for dev / when stripe not configured)
        g.user.credit(cents)
        db.session.commit()
        flash(f"Simulated top-up of {format_cents(cents)}")
        return redirect(url_for("wallet"))

@app.route("/stripe/success")
def stripe_success():
    return render_template_string("{% extends base %}{% block body %}<h3>Thank you — if this was real Stripe, the webhook will credit your account shortly.</h3><a href='/'>Home</a>{% endblock %}", base=BASE_HTML)

@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", None)
    if stripe and STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except Exception as e:
            logger.exception("Webhook signature verification failed")
            return "", 400
    else:
        # If no stripe configured, allow a simple JSON body with session info for testing
        try:
            event = json.loads(payload)
        except Exception:
            return "", 400

    # Handle checkout.session.completed
    if event.get("type") == "checkout.session.completed" or event.get("type") == "checkout.session.async_payment_succeeded":
        sess = event["data"]["object"]
        # client_reference_id contains our user id if we set it earlier
        client_ref = sess.get("client_reference_id")
        amount_total = sess.get("amount_total") or sess.get("amount_subtotal") or 0
        if client_ref:
            try:
                uid = int(client_ref)
                user = User.query.get(uid)
                if user:
                    user.credit(int(amount_total))
                    db.session.commit()
                    logger.info(f"Credited user {user.id} {amount_total} cents via webhook")
            except Exception:
                logger.exception("Failed to credit user from webhook")
    return "", 200

# -----------------------
# Products
# -----------------------
@app.route("/products")
def products_list():
    items = Product.query.order_by(Product.created_at.desc()).all()
    return render_template_string("{% extends base %}{% block body %}<h2>Products</h2><a class='btn btn-success mb-3' href='/product/new'>Sell Product</a><div class='row'>{% for p in items %}<div class='col-md-4'><div class='card mb-3'><img src='{{ p.image_url or \"https://via.placeholder.com/300x150\" }}' class='card-img-top card-img'><div class='card-body'><h5 class='card-title'>{{p.title}}</h5><p class='card-text small-muted'>{{ p.description[:120] }}</p><p class='card-text'><strong>{{ format_cents(p.price_cents) }}</strong></p><a class='btn btn-primary' href='/product/{{p.id}}'>View</a></div></div></div>{% endfor %}</div>{% endblock %}", base=BASE_HTML, items=items, format_cents=format_cents)

@app.route("/product/new", methods=["GET","POST"])
def product_new():
    if not g.user:
        flash("Login required"); return redirect(url_for("login"))
    if request.method == "POST":
        title = request.form.get("title","")
        desc = request.form.get("description","")
        price = request.form.get("price","0")
        image_url = request.form.get("image_url","")
        try:
            price_dec = Decimal(price)
        except Exception:
            flash("Invalid price"); return redirect(url_for("product_new"))
        p = Product(title=title, description=desc, price_cents=int(price_dec*100), image_url=image_url or None, seller_id=g.user.id)
        db.session.add(p); db.session.commit()
        flash("Product listed"); return redirect(url_for("products_list"))
    return render_template_string("{% extends base %}{% block body %}<h2>Sell Product</h2><form method=post><div class='mb-3'><input name='title' class='form-control' placeholder='Title'></div><div class='mb-3'><input name='image_url' class='form-control' placeholder='Image URL (optional)'></div><div class='mb-3'><textarea name='description' class='form-control' placeholder='Description'></textarea></div><div class='mb-3'><input name='price' class='form-control' placeholder='Price (e.g. 9.99)'></div><button class='btn btn-primary'>List Product</button></form>{% endblock %}", base=BASE_HTML)

@app.route("/product/<int:product_id>")
def product_view(product_id):
    p = Product.query.get_or_404(product_id)
    return render_template_string("{% extends base %}{% block body %}<div class='row'><div class='col-md-6'><img src='{{ p.image_url or \"https://via.placeholder.com/600x300\" }}' class='img-fluid'></div><div class='col-md-6'><h2>{{ p.title }}</h2><p>{{ p.description }}</p><p><strong>{{ format_cents(p.price_cents) }}</strong></p>{% if g.user and g.user.id != p.seller_id %}<form method=post action='/product/{{ p.id }}/buy'><button class='btn btn-success'>Buy</button></form>{% elif not g.user %}<a class='btn btn-primary' href='/login'>Login to Buy</a>{% else %}<span class='text-muted'>Your listing</span>{% endif %}</div></div>{% endblock %}", base=BASE_HTML, p=p, format_cents=format_cents)

@app.route("/product/<int:product_id>/buy", methods=["POST"])
def product_buy(product_id):
    if not g.user:
        flash("Login required"); return redirect(url_for("login"))
    p = Product.query.get_or_404(product_id)
    if p.seller_id == g.user.id:
        flash("Cannot buy your own product"); return redirect(url_for("product_view", product_id=product_id))
    amount = p.price_cents
    commission = calculate_commission_cents(amount)
    seller_receive = amount - commission
    if g.user.balance_cents < amount:
        flash("Insufficient balance"); return redirect(url_for("product_view", product_id=product_id))
    g.user.debit(amount)
    seller = User.query.get(p.seller_id)
    if seller:
        seller.credit(seller_receive)
    order = Order(buyer_id=g.user.id, product_id=p.id, amount_cents=amount, commission_cents=commission)
    db.session.add(order); db.session.commit()
    flash("Purchase complete")
    return redirect(url_for("products_list"))

# -----------------------
# Debt marketplace & investor logic
# -----------------------
@app.route("/debts")
def debt_list():
    items = DebtListing.query.order_by(DebtListing.created_at.desc()).all()
    return render_template_string("{% extends base %}{% block body %}<h2>Debt Market</h2><a class='btn btn-success mb-3' href='/debt/new'>List Debt</a><div>{% for d in items %}<div class='mb-2'><a href='/debt/{{d.id}}'>{{d.title}}</a> — {{ format_cents(d.principal_cents) }} ({{ '%.2f' % (d.current_rate_percent*100) }}%)</div>{% endfor %}</div>{% endblock %}", base=BASE_HTML, items=items, format_cents=format_cents)

@app.route("/debt/new", methods=["GET","POST"])
def debt_new():
    if not g.user:
        flash("Login required"); return redirect(url_for("login"))
    if request.method == "POST":
        title = request.form.get("title","")
        principal = request.form.get("principal","0")
        rate = request.form.get("rate","0.1")
        target = request.form.get("target","")
        try:
            pdec = Decimal(principal)
            rfloat = float(rate)
            targ = Decimal(target) if target else pdec
        except Exception:
            flash("Invalid input"); return redirect(url_for("debt_new"))
        d = DebtListing(title=title, principal_cents=int(pdec*100), interest_rate_percent=rfloat, seller_id=g.user.id)
        d.current_rate_percent = rfloat
        d.target_amount_cents = int(targ*100)
        db.session.add(d); db.session.commit()
        flash("Debt listed"); return redirect(url_for("debt_list"))
    return render_template_string("{% extends base %}{% block body %}<h2>List Debt</h2><form method=post><div class='mb-3'><input name='title' class='form-control' placeholder='Title'></div><div class='mb-3'><input name='principal' class='form-control' placeholder='Principal (e.g. 1000.00)'></div><div class='mb-3'><input name='rate' class='form-control' placeholder='Interest rate decimal (0.12)'></div><div class='mb-3'><input name='target' class='form-control' placeholder='Target amount (optional)'></div><button class='btn btn-primary'>List</button></form>{% endblock %}", base=BASE_HTML)

@app.route("/debt/<int:debt_id>")
def debt_view(debt_id):
    d = DebtListing.query.get_or_404(debt_id)
    return render_template_string("{% extends base %}{% block body %}<h2>{{d.title}}</h2><p>Principal: {{ format_cents(d.principal_cents) }}</p><p>Rate: {{ '%.2f' % (d.current_rate_percent*100) }}%</p>{% if g.user and g.user.id != d.seller_id %}<form method=post action='/debt/{{d.id}}/buy'>Amount:<input name='amount' class='form-control' placeholder='Amount (e.g. 50.00)'><button class='btn btn-success mt-2'>Buy</button></form>{% elif not g.user %}<a class='btn btn-primary' href='/login'>Login to Buy</a>{% else %}<span class='text-muted'>Your listing</span>{% endif %}{% endblock %}", base=BASE_HTML, d=d, format_cents=format_cents)

@app.route("/debt/<int:debt_id>/buy", methods=["POST"])
def debt_buy(debt_id):
    if not g.user:
        flash("Login required"); return redirect(url_for("login"))
    d = DebtListing.query.get_or_404(debt_id)
    amt = request.form.get("amount","0")
    try:
        dec = Decimal(amt)
    except Exception:
        flash("Invalid amount"); return redirect(url_for("debt_view", debt_id=debt_id))
    cents = int(dec*100)
    if g.user.balance_cents < cents:
        flash("Insufficient balance"); return redirect(url_for("debt_view", debt_id=debt_id))
    # debit buyer
    g.user.debit(cents)
    # create position
    pos = DebtPosition(owner_id=g.user.id, debt_id=d.id, principal_cents=cents)
    db.session.add(pos)
    # commission & credit seller
    commission = calculate_commission_cents(cents)
    seller_receive = cents - commission
    seller = User.query.get(d.seller_id)
    if seller:
        seller.credit(seller_receive)
    d.total_invested_cents = (d.total_invested_cents or 0) + cents

    # dynamic rate adjustment — aggressive option enabled
    target = d.target_amount_cents or 1
    ratio = (d.total_invested_cents / target) if target else 0
    # aggressive volatility: large jumps possible (per your request)
    if ratio < 0.01:
        d.current_rate_percent = min(1.0, d.current_rate_percent + 0.80)
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
    flash("Debt purchased")
    return redirect(url_for("debt_view", debt_id=debt_id))

# -----------------------
# Investor dashboard & ROI
# -----------------------
@app.route("/investor")
def investor_dashboard():
    if not g.user:
        return redirect(url_for("login"))
    positions = DebtPosition.query.filter_by(owner_id=g.user.id).all()
    # compute simple ROI: principal * current_rate_percent * days_held / 365
    rows = []
    now = datetime.now(timezone.utc)
    for p in positions:
        days = (now - p.created_at.replace(tzinfo=timezone.utc)).days
        rate = p.debt.current_rate_percent or p.debt.interest_rate_percent
        est_interest = Decimal(p.principal_cents) * Decimal(rate) * Decimal(days) / Decimal(365)
        rows.append({
            "position": p,
            "days": days,
            "est_interest_cents": int(est_interest),
            "rate": rate
        })
    return render_template_string("{% extends base %}{% block body %}<h2>Investor Dashboard</h2><p>Positions:</p><table class='table'><thead><tr><th>Debt</th><th>Principal</th><th>Rate</th><th>Days Held</th><th>Est Interest</th></tr></thead><tbody>{% for r in rows %}<tr><td>{{ r.position.debt.title }}</td><td>{{ format_cents(r.position.principal_cents) }}</td><td>{{ '%.2f' % (r.rate*100) }}%</td><td>{{ r.days }}</td><td>{{ format_cents(r.est_interest_cents) }}</td></tr>{% endfor %}</tbody></table>{% endblock %}", base=BASE_HTML, rows=rows, format_cents=format_cents)

# -----------------------
# Payouts & admin
# -----------------------
@app.route("/request-payout", methods=["POST"])
def request_payout():
    if not g.user:
        return redirect(url_for("login"))
    amount = request.form.get("amount","0")
    try:
        dec = Decimal(amount)
    except Exception:
        flash("Invalid amount"); return redirect(url_for("wallet"))
    cents = int(dec*100)
    if g.user.balance_cents < cents:
        flash("Insufficient balance"); return redirect(url_for("wallet"))
    # create payout request and debit immediately to avoid double claiming
    g.user.debit(cents)
    req = PayoutRequest(user_id=g.user.id, amount_cents=cents, paid=False)
    db.session.add(req); db.session.commit()
    flash("Payout requested — admin will process")
    return redirect(url_for("wallet"))

@app.route("/admin")
def admin_panel():
    if not g.user or not g.user.is_admin:
        flash("Admin required"); return redirect(url_for("index"))
    users = User.query.order_by(User.created_at.desc()).all()
    payouts = PayoutRequest.query.order_by(PayoutRequest.created_at.desc()).all()
    total_commission = db.session.query(db.func.sum(Order.commission_cents)).scalar() or 0
    return render_template_string("{% extends base %}{% block body %}<h2>Admin</h2><p>Total commission: {{ format_cents(total_commission) }}</p><h3>Payout Requests</h3><table class='table'><thead><tr><th>User</th><th>Amount</th><th>Paid</th><th>Action</th></tr></thead><tbody>{% for p in payouts %}<tr><td>{{ p.user.username }}</td><td>{{ format_cents(p.amount_cents) }}</td><td>{{ p.paid }}</td><td>{% if not p.paid %}<form method='post' action='/admin/payout/{{ p.id }}'><button class='btn btn-sm btn-success'>Mark Paid</button></form>{% else %}—{% endif %}</td></tr>{% endfor %}</tbody></table>{% endblock %}", base=BASE_HTML, payouts=payouts, format_cents=format_cents, total_commission=total_commission)

@app.route("/admin/payout/<int:pay_id>", methods=["POST"])
def admin_payout(pay_id):
    if not g.user or not g.user.is_admin:
        flash("Admin required"); return redirect(url_for("index"))
    pr = PayoutRequest.query.get_or_404(pay_id)
    pr.paid = True
    db.session.commit()
    flash("Marked paid (simulate sending funds)")
    return redirect(url_for("admin_panel"))

# -----------------------
# Health, init, debug
# -----------------------
@app.route("/healthz")
def healthz():
    return "ok"

@app.route("/init-db")
def init_db_route():
    # idempotent creation
    db.create_all()
    return "Database initialized!"

# -----------------------
# Auto-create DB and admin at import time
# -----------------------
with app.app_context():
    try:
        db.create_all()
        if not User.query.filter_by(username="admin").first():
            admin = User(username="admin", is_admin=True)
            admin.set_password(os.getenv("ADMIN_PASS","adminpass"))
            admin.credit(100000)
            db.session.add(admin); db.session.commit()
            print("🔧 created admin user 'admin'")
        print("🔥 DB ready")
    except Exception as e:
        print("❌ DB create_all error:", e)

# intentionally no app.run(); run using gunicorn: gunicorn lively_marketplace_app:app
