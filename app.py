from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
import os

app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tavern.db'
db = SQLAlchemy(app)

# ==================== TABLES ====================
class User(db.Model):
    id       = db.Column(db.Integer, primary_key=True)
    name     = db.Column(db.String(80), nullable=False)
    email    = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    role     = db.Column(db.String(20), default='barstaff')

class Rota(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    month     = db.Column(db.String(7), nullable=False)   # 2025-11
    day       = db.Column(db.Integer, nullable=False)     # 1-31
    user_id   = db.Column(db.Integer, db.ForeignKey('user.id'))
    published = db.Column(db.Boolean, default=False)      # LOCK

class Request(db.Model):   # day-off requests
    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date    = db.Column(db.Date, nullable=False)
    status  = db.Column(db.String(20), default='pending')  # pending / approved / declined
    note    = db.Column(db.Text)

# ==================== DEMO DATA ====================
def ensure_demo():
    if not User.query.filter_by(email='tav@tavern.com').first():
        tav = User(name='Tav', email='tav@tavern.com', password='pint123', role='admin')
        db.session.add(tav)
        for n in ['Finn','Heather','Autumn','Nathan']:
            db.session.add(User(name=n, email=f'{n.lower()}@tavern.com', password='pint123'))
        db.session.commit()

# ==================== ROUTES ====================
@app.route('/')
def hello():
    return "Tavern Rota API is running."

# ---------- AUTH ----------
@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    user = User.query.filter_by(email=data['email'], password=data['password']).first()
    if not user:
        return jsonify({'error':'Bad credentials'}), 401
    return jsonify({'id':user.id, 'name':user.name, 'role':user.role})

# ---------- ROTA ----------
@app.route('/api/rota/<month>')
def get_rota(month):
    published = Rota.query.filter_by(month=month).first().published if Rota.query.filter_by(month=month).first() else False
    rows = db.session.query(Rota.day, User.name, Rota.published)\
                     .join(User, Rota.user_id == User.id)\
                     .filter(Rota.month == month)\
                     .all()
    return jsonify({'published':published,
                    'days':{day:name for day,name,publ in rows}})

@app.route('/api/rota/<month>/generate', methods=['POST'])
def generate_rota(month):
    Rota.query.filter_by(month=month).delete()
    staff = User.query.filter_by(role='barstaff').all()
    if not staff:
        return jsonify({'error':'No barstaff'}), 400
    for d in range(1, 32):
        idx = (d - 1) % len(staff)
        db.session.add(Rota(month=month, day=d, user_id=staff[idx].id))
    db.session.commit()
    return jsonify({'ok':True})

@app.route('/api/rota/<month>/publish', methods=['POST'])
def publish_rota(month):
    Rota.query.filter_by(month=month).update({'published':True})
    db.session.commit()
    return jsonify({'ok':True})

# ---------- DAY-OFF REQUESTS ----------
@app.route('/api/requests', methods=['GET'])
def list_requests():
    args = request.args
    q = db.session.query(Request.id, Request.date, Request.status, Request.note, User.name)\
                  .join(User)
    if 'user' in args:
        q = q.filter(Request.user_id == args['user'])
    return jsonify([{'id':r.id,'date':r.date.isoformat(),'status':r.status,'note':r.note,'name':r.name} for r in q])

@app.route('/api/requests', methods=['POST'])
def create_request():
    data = request.get_json()
    req = Request(user_id=data['user_id'],
                  date=datetime.datetime.strptime(data['date'],'%Y-%m-%d').date(),
                  note=data.get('note',''))
    db.session.add(req)
    db.session.commit()
    return jsonify({'ok':True})

@app.route('/api/requests/<int:req_id>', methods=['PATCH'])
def decide_request(req_id):
    data = request.get_json()
    Request.query.filter_by(id=req_id).update({'status':data['status']})
    db.session.commit()
    return jsonify({'ok':True})

# ---------- START ----------
with app.app_context():
    db.create_all()
    ensure_demo()

if __name__ == '__main__':
    app.run(debug=True)
