from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
import datetime, os, calendar

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
    contracted_hours = db.Column(db.Float, default=40.0)   # NEW

class Rota(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    month     = db.Column(db.String(7), nullable=False)   # 2025-11
    day       = db.Column(db.Integer, nullable=False)
    user_id   = db.Column(db.Integer, db.ForeignKey('user.id'))
    published = db.Column(db.Boolean, default=False)

class Hours(db.Model):        # clock-in / clock-out
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

# ==================== DEMO DATA ====================
def ensure_demo():
    if not User.query.filter_by(email='tav@tavern.com').first():
        tav = User(name='Tav', email='tav@tavern.com', password='pint123', role='admin', contracted_hours=40)
        db.session.add(tav)
        for n,h in [('Finn',35),('Heather',30),('Autumn',40),('Nathan',40)]:
            db.session.add(User(name=n, email=f'{n.lower()}@tavern.com', password='pint123', contracted_hours=h))
        db.session.commit()

# ==================== HELPERS ====================
def month_weekdays(year, month):
    """return number of Mon-Fri in month"""
    cal = calendar.Calendar()
    return len([d for d in cal.itermonthdays(year, month) if d and cal.weekday(year, month, d) < 5])

def worked_in_month(user_id, month_str):
    y, m = map(int, month_str.split('-'))
    q = db.session.query(db.func.sum(
            db.func.strftime('%s', Hours.finish) - db.func.strftime('%s', Hours.start)
         ))\
        .filter(Hours.user_id == user_id,
                db.func.strftime('%Y-%m', Hours.date) == month_str)\
        .scalar()
    return (q or 0) / 3600   # seconds → hours

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
    return jsonify({'id':user.id, 'name':user.name, 'role':user.role,
                    'contracted_hours':user.contracted_hours})

# ---------- ROTA ----------
@app.route('/api/rota/<month>')
def get_rota(month):
    published = bool(Rota.query.filter_by(month=month).first().published if Rota.query.filter_by(month=month).first() else False)
    rows = db.session.query(Rota.day, User.name, User.contracted_hours, Rota.published)\
                     .join(User, Rota.user_id == User.id)\
                     .filter(Rota.month == month)\
                     .all()
    days = {day: {'name':name,'contracted':ctr} for day,name,ctr,publ in rows}
    # hours worked so far
    hours_info = {}
    for u in User.query.filter_by(role='barstaff').all():
        worked = worked_in_month(u.id, month)
        needed = u.contracted_hours - worked
        hours_info[u.name] = {'worked':round(worked,1),
                              'needed':round(needed,1),
                              'contracted':u.contracted_hours,
                              'pc': round((worked/u.contracted_hours)*100,0) if u.contracted_hours else 0}
    return jsonify({'published':published, 'days':days, 'hours':hours_info})

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

# ---------- HOURS (log worked) ----------
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

# ---------- DAY-OFF REQUESTS ----------
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

# ---------- REPORT (cron friendly) ----------
@app.route('/api/report/<month>')
def report(month):
    y, m = map(int, month.split('-'))
    staff = User.query.filter_by(role='barstaff').all()
    out = {}
    for s in staff:
        worked = worked_in_month(s.id, month)
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
