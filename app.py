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
    month      = db.Column(db.String(7), nullable=False)   # 2025-11
    day        = db.Column(db.Integer, nullable=False)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'))
    start      = db.Column(db.String(5), default='')       # HH:MM
    finish     = db.Column(db.String(5), default='')
    slot       = db.Column(db.Integer, default=1)          # 1 or 2
    published  = db.Column(db.Boolean, default=False)

# ==================== DEMO DATA ====================
def ensure_demo():
    if not User.query.filter_by(email='admin@tavern.com').first():
        admin = User(name='Dean', email='admin@tavern.com', password='pint123', role='admin', contracted_hours=0)
        h     = User(name='Heather', email='heather@tavern.com', password='pint123', contracted_hours=80)
        f     = User(name='Finn', email='finn@tavern.com', password='pint123', contracted_hours=140)
        db.session.add_all([admin,h,f])
        db.session.commit()

# ==================== HELPERS ====================
DAILY = {0:('14:00','22:00'),   # Mon
         1:('14:00','22:00'),   # Tue
         2:('14:00','22:00'),   # Wed
         3:('14:00','22:00'),   # Thu
         4:('14:00','00:00'),   # Fri  (10h)
         5:('12:00','22:00'),   # Sat  (10h)
         6:('12:00','17:00')}   # Sun  (5h)

def close_time(weekday):
    return DAILY[weekday][1]

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

# ---------- ROTA ----------
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
        st, fn = DAILY[wd]
        # slot 1
        idx = (d-1) % len(staff)
        db.session.add(Rota(month=month, day=d, user_id=staff[idx].id,
                            start=st, finish=fn, slot=1))
        # slot 2  (Fri/Sat only)
        if wd in (4,5):
            db.session.add(Rota(month=month, day=d, user_id=staff[(idx+1)%len(staff)].id,
                                start='', finish=fn, slot=2))
    db.session.commit()
    return jsonify({'ok':True})

@app.route('/api/rota/<month>/slot/<int:day>/<int:slot>', methods=['PATCH'])
def set_slot_time(month, day, slot):
    data = request.get_json()
    Rota.query.filter_by(month=month, day=day, slot=slot)\
              .update({'start':data['start']})
    db.session.commit()
    return jsonify({'ok':True})

@app.route('/api/rota/<month>/publish', methods=['POST'])
def publish_rota(month):
    Rota.query.filter_by(month=month).update({'published':True})
    db.session.commit()
    return jsonify({'ok':True})

# ---------- HOURS (simple logger) ----------
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

# ---------- REPORT (hours worked this month) ----------
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
                       'balance':round(s.contracted_hours - worked,1)}
    return jsonify(out)

# ---------- START ----------
with app.app_context():
    db.create_all()
    ensure_demo()

if __name__ == '__main__':
    app.run(debug=True)
