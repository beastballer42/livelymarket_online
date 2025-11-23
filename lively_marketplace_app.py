# lively_marketplace_app.py - compact edition
from flask import Flask, request, session, redirect, url_for, render_template_string, flash, g, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from decimal import Decimal
from datetime import datetime
import os, threading, time, logging

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL','sqlite:///lively_marketplace.db')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY','dev-secret')
db = SQLAlchemy(app)
with app.app_context():
    try:
        db.create_all()
        print("🔥 Database tables ensured")
    except Exception as e:
        print("❌ Table creation error:", e)
logger = logging.getLogger('lively')
logger.addHandler(logging.StreamHandler())

# Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    balance_cents = db.Column(db.Integer, default=0)
    is_admin = db.Column(db.Boolean, default=False)
    stripe_account_id = db.Column(db.String(200), nullable=True)
    def set_password(self,p): self.password_hash = generate_password_hash(p)
    def check_password(self,p): return check_password_hash(self.password_hash,p)
    def credit(self,c): self.balance_cents = (self.balance_cents or 0) + int(c)
    def debit(self,c): self.balance_cents = (self.balance_cents or 0) - int(c)

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
    active = db.Column(db.Boolean, default=True)
    owner = db.relationship('User')
    debt = db.relationship('DebtListing')

# Helpers
def format_cents(c):
    return f"${Decimal(c)/100:.2f}"

@app.before_request
def load_user():
    g.user = None
    if 'user_id' in session:
        g.user = User.query.get(session['user_id'])

# Routes minimal
@app.route('/')
def index():
    debts = DebtListing.query.order_by(DebtListing.created_at.desc()).limit(6).all()
    return render_template_string('<h1>Welcome</h1><ul>{% for d in debts %}<li><a href="/debt/{{d.id}}">{{d.title}} - {{format_cents(d.principal_cents)}}</a></li>{% endfor %}</ul>', debts=debts, format_cents=format_cents)

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method=='POST':
        u = request.form.get('username','').strip()
        p = request.form.get('password','')
        if not u or not p: flash('bad'); return redirect(url_for('register'))
        if User.query.filter_by(username=u).first(): flash('exists'); return redirect(url_for('register'))
        usr = User(username=u); usr.set_password(p); db.session.add(usr); db.session.commit(); session['user_id']=usr.id; return redirect(url_for('index'))
    return render_template_string('<form method=post>Username:<input name=username> Password:<input name=password type=password><button>Register</button></form>')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        u = request.form.get('username',''); p = request.form.get('password','')
        usr = User.query.filter_by(username=u).first()
        if not usr or not usr.check_password(p): flash('invalid'); return redirect(url_for('login'))
        session['user_id']=usr.id; return redirect(url_for('index'))
    return render_template_string('<form method=post>Username:<input name=username> Password:<input name=password type=password><button>Login</button></form>')

@app.route('/debt/new', methods=['GET','POST'])
def debt_new():
    if not g.user: return redirect(url_for('login'))
    if request.method=='POST':
        title = request.form.get('title','')
        principal = Decimal(request.form.get('principal','0'))
        rate = float(request.form.get('rate','0.1'))
        d = DebtListing(title=title, principal_cents=int(principal*100), interest_rate_percent=rate, seller_id=g.user.id)
        d.current_rate_percent = rate; d.target_amount_cents = int(request.form.get('target', principal*100))
        db.session.add(d); db.session.commit(); return redirect(url_for('index'))
    return render_template_string('<form method=post>Title:<input name=title>Principal:<input name=principal>Rate (0.12):<input name=rate>Target(optional):<input name=target><button>List</button></form>')

@app.route('/debt/<int:debt_id>')
def debt_view(debt_id):
    d = DebtListing.query.get_or_404(debt_id)
    return render_template_string('<h2>{{d.title}}</h2><p>Principal: {{format_cents(d.principal_cents)}}</p><p>Rate: {{"%.2f" % (d.current_rate_percent*100)}}%</p><form method=post action="/debt/{}/buy">Amount:<input name=amount><button>Buy</button></form>'.format(debt_id), d=d, format_cents=format_cents)

@app.route('/debt/<int:debt_id>/buy', methods=['POST'])
def debt_buy(debt_id):
    if not g.user: return redirect(url_for('login'))
    d = DebtListing.query.get_or_404(debt_id)
    amt = Decimal(request.form.get('amount','0'))
    cents = int(amt*100)
    if g.user.balance_cents < cents: flash('insufficient'); return redirect(url_for('debt_view', debt_id=debt_id))
    g.user.debit(cents); pos = DebtPosition(owner_id=g.user.id, debt_id=d.id, principal_cents=cents); db.session.add(pos); d.total_invested_cents = (d.total_invested_cents or 0) + cents; db.session.commit()
    # update rate (simple)
    target = d.target_amount_cents or 1
    ratio = d.total_invested_cents/target
    if ratio < 0.01: d.current_rate_percent = min(0.95, d.current_rate_percent + 0.20)
    elif ratio < 0.05: d.current_rate_percent = min(0.95, d.current_rate_percent + 0.12)
    elif ratio < 0.2: d.current_rate_percent = min(0.95, d.current_rate_percent + 0.06)
    elif ratio < 0.5: d.current_rate_percent = max(0.001, d.current_rate_percent + 0.02)
    elif ratio < 1.0: d.current_rate_percent = max(0.001, d.current_rate_percent - 0.03)
    elif ratio < 1.5: d.current_rate_percent = max(0.001, d.current_rate_percent - 0.12)
    else: d.current_rate_percent = max(0.0001, d.current_rate_percent - 0.30)
    db.session.commit()
    flash('bought'); return redirect(url_for('debt_view', debt_id=debt_id))

if __name__ == '__main__':
      # -------- Database Auto-Init on Startup -------- #
    @app.before_first_request
    def initialize_database():
        try:
            db.create_all()
            print("🔥 Tables verified/created successfully")
        except Exception as e:
            print("❌ DB Init Error:", e)

