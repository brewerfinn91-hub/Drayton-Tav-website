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

class Hours(db.Model):
    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date    = db.Column(db.Date, nullable=False)
    start   = db.Column(db.DateTime)
    finish  = db.Column(db.DateTime)

class Request(db.Model):
    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date    = db.Column(db.Date, nullable=False)
    status  = db.Column(db.String(20), default='pending')
    note    = db.Column(db.Text)

# ===== FORCE RESET DEMO =====
with app.app_context():
    db.drop_all()
    db.create_all()
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

# ---------- ROTA (auto-removes approved day-off) ----------
@app.route('/api/rota/<month>')
def get_rota(month):
    published = bool(Rota.query.filter_by(month=month).first().published if Rota.query.filter_by(month=month).first() else False)
    # exclude staff on approved day-off
    approved_dates = db.session.query(Request.user_id, Request.date)\
                               .filter(Request.status=='approved', Request.date.like(month+'-%'))\
                               .all()
    banned = {(u,d.strftime('%Y-%m-%d')) for u,d in approved_dates}

    rows = db.session.query(Rota.day, Rota.slot, Rota.start, Rota.finish, User.name, User.id)\
                     .join(User)\
                     .filter(Rota.month == month)\
                     .order_by(Rota.day, Rota.slot)\
                     .all()
    out = {}
    for day,slot,start,finish,name,uid in rows:
        if (uid, f'{month}-{day:02d}') in banned: continue   # skip approved day-off
        out.setdefault(day,[]).append({'slot':slot,'name':name,'start':start,'finish':finish})
    return jsonify({'published':published, 'days':out})

@app.route('/api/rota/<month>/generate', methods=['POST'])
def generate_rota(month):
    y,m = map(int, month.split('-'))
    Rota.query.filter_by(month=month).delete()
    staff = User.query.filter_by(role='barstaff').all()
    if not staff: return jsonify({'error':'No barstaff'}), 400
    # exclude approved day-off
    approved_dates = db.session.query(Request.user_id, Request.date)\
                               .filter(Request.status=='approved', Request.date.like(month+'-%'))\
                               .all()
    banned = {(u,d) for u,d in approved_dates}

    cal = calendar.Calendar()
    for d in cal.itermonthdays(y,m):
        if d==0: continue
        wd = datetime.date(y,m,d).weekday()
        st, fn = ('14:00','22:00') if wd<4 else ('12:00','22:00') if wd==5 else ('12:00','17:00')
        available = [s for s in staff if (s.id, datetime.date(y,m,d)) not in banned]
        if not available: continue
        # slot 1
        idx = (d-1) % len(available)
        db.session.add(Rota(month=month, day=d, user_id=available[idx].id, start=st, finish=fn, slot=1))
        # slot 2  (Fri/Sat only)
        if wd in (4,5) and len(available)>1:
            db.session.add(Rota(month=month, day=d, user_id=available[(idx+1)%len(available)].id, start='', finish=fn, slot=2))
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

@app.route('/api/hours/<month>/<int:user_id>', methods=['GET'])
def get_hours(month, user_id):
    q = db.session.query(db.func.sum(
            db.func.strftime('%s', Hours.finish) - db.func.strftime('%s', Hours.start)
        ))\
        .filter(Hours.user_id == user_id,
                db.func.strftime('%Y-%m', Hours.date) == month)\
        .scalar()
    worked = (q or 0) / 3600
    return jsonify({'worked':round(worked,1)})

# ---------- REQUESTS ----------
@app.route('/api/requests', methods=['GET'])
def list_requests():
    admin = request.args.get('admin')
    q = db.session.query(Request.id, Request.date, Request.status, Request.note, User.name, User.id.label('user_id'))\
                  .join(User)
    if admin != '1':
        q = q.filter(Request.user_id == request.args.get('user'))
    return jsonify([{'id':r.id,'date':r.date.isoformat(),'status':r.status,'note':r.note,'name':r.name,'user_id':r.user_id} for r in q])

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

# ---------- USERS (admin only) ----------
@app.route('/api/users', methods=['GET'])
def list_users():
    users = User.query.all()
    return jsonify([{'id':u.id,'name':u.name,'email':u.email,'role':u.role,'contracted_hours':u.contracted_hours} for u in users])

@app.route('/api/users', methods=['POST'])
def add_user():
    data = request.get_json()
    u = User(name=data['name'], email=data['email'], password=data['password'],
             role=data.get('role','barstaff'), contracted_hours=data.get('contracted_hours',0))
    db.session.add(u)
    db.session.commit()
    return jsonify({'id':u.id})

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    User.query.filter_by(id=user_id).delete()
    db.session.commit()
    return jsonify({'ok':True})

@app.route('/api/users/<int:user_id>', methods=['PATCH'])
def edit_user(user_id):
    data = request.get_json()
    User.query.filter_by(id=user_id).update(data)
    db.session.commit()
    return jsonify({'ok':True})

# ---------- MONTHLY REPORT (hours worked vs contracted) ----------
@app.route('/api/report/<month>')
def report(month):
    y, m = map(int, month.split('-'))
    staff = User.query.filter_by(role='barstaff').all()
    out = {}
    for s in staff:
        q = db.session.query(db.func.sum(
                db.func.strftime('%s', Hours.finish) - db.func.strftime('%s', Hours.start)
            ))\
            .filter(Hours.user_id == s.id,
                    db.func.strftime('%Y-%m', Hours.date) == month)\
            .scalar()
        worked = (q or 0) / 3600
        out[s.name] = {'contracted':s.contracted_hours,
                       'worked':round(worked,1),
                       'needed':round(s.contracted_hours - worked,1)}
    return jsonify(out)

# ---------- START ----------
if __name__ == '__main__':
    app.run(debug=True)
