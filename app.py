from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
import datetime, calendar, os

app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tavern.db'
db = SQLAlchemy(app)

# ==================== TABLES ====================
class User(db.Model):
    id               = db.Column(db.Integer, primary_key=True)
    name             = db.Column(db.String(80), nullable=False)
    email            = db.Column(db.String(120), unique=True, nullable=False)
    password         = db.Column(db.String(120), nullable=False)
    role             = db.Column(db.String(20), default='barstaff')
    contracted_hours = db.Column(db.Float, default=0.0)

class Rota(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    month      = db.Column(db.String(7), nullable=False)
    day        = db.Column(db.Integer, nullable=False)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'))
    start      = db.Column(db.String(5), default='')
    finish     = db.Column(db.String(5), default='')
    slot       = db.Column(db.Integer, default=1)
    published  = db.Column(db.Boolean, default=False)

# ==================== DEMO DATA ====================
def ensure_demo():
    if not User.query.filter_by(email='dean@tavern.com').first():
        dean    = User(name='Dean', email='dean@tavern.com', password='dean', role='admin', contracted_hours=0)
        heather = User(name='Heather', email='heather@tavern.com', password='heather', contracted_hours=80)
        finn    = User(name='Finn', email='finn@tavern.com', password='finn', contracted_hours=140)
        db.session.add_all([dean, heather, finn])
        db.session.commit()

# ==================== ROUTES ====================
@app.route('/')
def hello():
    return "Tavern Rota API is running."

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    user = User.query.filter_by(email=data['email'], password=data['password']).first()
    if not user:
        return jsonify({'error':'Bad credentials'}), 401
    return jsonify({'id':user.id, 'name':user.name, 'role':user.role,
                    'contracted_hours':user.contracted_hours})

@app.route('/api/rota/<month>')
def get_rota(month):
    published = bool(Rota.query.filter_by(month=month).first().published if Rota.query.filter_by(month=month).first() else False)
    rows = db.session.query(Rota.day, Rota.slot, Rota.start, Rota.finish, User.name)\
                     .join(User)\
                     .filter(Rota.month == month)\
                     .order_by(Rota.day, Rota.slot)\
                     .all()
    out = {}
    for day,slot,start,finish,name in rows:
        out.setdefault(day,[]).append({'slot':slot,'name':name,'start':start,'finish':finish})
    return jsonify({'published':published, 'days':out})

@app.route('/api/rota/<month>/generate', methods=['POST'])
def generate_rota(month):
    y,m = map(int, month.split('-'))
    Rota.query.filter_by(month=month).delete()
    staff = User.query.filter_by(role='barstaff').all()
    if not staff: return jsonify({'error':'No barstaff'}), 400
    cal = calendar.Calendar()
    for d in cal.itermonthdays(y,m):
        if d==0: continue
        wd = datetime.date(y,m,d).weekday()
        st, fn = ('14:00','22:00') if wd<4 else ('12:00','22:00') if wd==5 else ('12:00','17:00')
        # slot 1
        idx = (d-1) % len(staff)
        db.session.add(Rota(month=month, day=d, user_id=staff[idx].id, start=st, finish=fn, slot=1))
        # slot 2  (Fri/Sat only)
        if wd in (4,5):
            db.session.add(Rota(month=month, day=d, user_id=staff[(idx+1)%len(staff)].id, start='', finish=fn, slot=2))
    db.session.commit()
    return jsonify({'ok':True})

@app.route('/api/rota/<month>/slot/<int:day>/<int:slot>', methods=['PATCH'])
def set_slot_time(month, day, slot):
    data = request.get_json()
    Rota.query.filter_by(month=month, day=day, slot=slot).update({'start':data['start']})
    db.session.commit()
    return jsonify({'ok':True})

@app.route('/api/rota/<month>/publish', methods=['POST'])
def publish_rota(month):
    Rota.query.filter_by(month=month).update({'published':True})
    db.session.commit()
    return jsonify({'ok':True})

# ---------- HOURS ----------
class Hours(db.Model):
    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date    = db.Column(db.Date, nullable=False)
    start   = db.Column(db.DateTime)
    finish  = db.Column(db.DateTime)

@app.route('/api/hours', methods=['POST'])
def log_hours():
    data = request.get_json()
    h = Hours(user_id=data['user_id'],
              date=datetime.datetime.strptime(data['date'],'%Y-%m-%d').date(),
              start=datetime.datetime.strptime(data['start'],'%H:%M'),
              finish=datetime.datetime.strptime(data['finish'],'%H:%M'))
    db.session.add(h)
    db.session.commit()
    return jsonify({'ok':True})

# ---------- REQUESTS ----------
class Request(db.Model):
    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date    = db.Column(db.Date, nullable=False)
    status  = db.Column(db.String(20), default='pending')
    note    = db.Column(db.Text)

@app.route('/api/requests', methods=['GET'])
def list_requests():
    admin = request.args.get('admin')
    q = db.session.query(Request.id, Request.date, Request.status, Request.note, User.name)\
                  .join(User)
    if admin != '1':
        q = q.filter(Request.user_id == request.args.get('user'))
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
