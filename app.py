from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'pressjobs_secret_2024'

DB_PATH = 'pressjobs.db'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        account_type TEXT NOT NULL,
        bio TEXT,
        location TEXT,
        skills TEXT,
        education TEXT,
        experience TEXT,
        profile_image TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        category TEXT NOT NULL,
        location TEXT,
        job_type TEXT,
        salary TEXT,
        requirements TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(channel_id) REFERENCES users(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        journalist_id INTEGER NOT NULL,
        message TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(journalist_id) REFERENCES users(id)
    )''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    conn = get_db()
    jobs = conn.execute('''
        SELECT jobs.*, users.name as channel_name, users.location as channel_location
        FROM jobs JOIN users ON jobs.channel_id = users.id
        ORDER BY jobs.created_at DESC LIMIT 6
    ''').fetchall()
    channels = conn.execute("SELECT * FROM users WHERE account_type='channel' LIMIT 4").fetchall()
    journalists = conn.execute("SELECT * FROM users WHERE account_type='journalist' LIMIT 6").fetchall()
    conn.close()
    return render_template('index.html', jobs=jobs, channels=channels, journalists=journalists)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])
        account_type = request.form['account_type']
        location = request.form.get('location', '')
        bio = request.form.get('bio', '')
        skills = request.form.get('skills', '')
        education = request.form.get('education', '')
        try:
            conn = get_db()
            conn.execute('''INSERT INTO users (name, email, password, account_type, location, bio, skills, education)
                           VALUES (?,?,?,?,?,?,?,?)''',
                        (name, email, password, account_type, location, bio, skills, education))
            conn.commit()
            conn.close()
            flash('تم إنشاء حسابك بنجاح! يمكنك تسجيل الدخول الآن', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('البريد الإلكتروني مستخدم بالفعل', 'error')
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['account_type'] = user['account_type']
            flash(f'مرحباً {user["name"]}!', 'success')
            return redirect(url_for('dashboard'))
        flash('بيانات الدخول غير صحيحة', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
    if session['account_type'] == 'channel':
        jobs = conn.execute('SELECT * FROM jobs WHERE channel_id=? ORDER BY created_at DESC', (session['user_id'],)).fetchall()
        applications = conn.execute('''
            SELECT applications.*, jobs.title as job_title, users.name as applicant_name, users.skills, users.bio
            FROM applications
            JOIN jobs ON applications.job_id = jobs.id
            JOIN users ON applications.journalist_id = users.id
            WHERE jobs.channel_id=?
            ORDER BY applications.created_at DESC
        ''', (session['user_id'],)).fetchall()
        conn.close()
        return render_template('dashboard_channel.html', user=user, jobs=jobs, applications=applications)
    else:
        my_apps = conn.execute('''
            SELECT applications.*, jobs.title as job_title, users.name as channel_name
            FROM applications
            JOIN jobs ON applications.job_id = jobs.id
            JOIN users ON jobs.channel_id = users.id
            WHERE applications.journalist_id=?
            ORDER BY applications.created_at DESC
        ''', (session['user_id'],)).fetchall()
        conn.close()
        return render_template('dashboard_journalist.html', user=user, applications=my_apps)

@app.route('/jobs')
def jobs():
    category = request.args.get('category', '')
    search = request.args.get('search', '')
    conn = get_db()
    query = '''SELECT jobs.*, users.name as channel_name FROM jobs
               JOIN users ON jobs.channel_id = users.id WHERE 1=1'''
    params = []
    if category:
        query += ' AND jobs.category=?'
        params.append(category)
    if search:
        query += ' AND (jobs.title LIKE ? OR jobs.description LIKE ?)'
        params.extend([f'%{search}%', f'%{search}%'])
    query += ' ORDER BY jobs.created_at DESC'
    all_jobs = conn.execute(query, params).fetchall()
    conn.close()
    return render_template('jobs.html', jobs=all_jobs, category=category, search=search)

@app.route('/job/<int:job_id>')
def job_detail(job_id):
    conn = get_db()
    job = conn.execute('''SELECT jobs.*, users.name as channel_name, users.bio as channel_bio, users.location as channel_location
                          FROM jobs JOIN users ON jobs.channel_id = users.id WHERE jobs.id=?''', (job_id,)).fetchone()
    conn.close()
    if not job:
        return redirect(url_for('jobs'))
    return render_template('job_detail.html', job=job)

@app.route('/apply/<int:job_id>', methods=['POST'])
def apply(job_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    message = request.form.get('message', '')
    conn = get_db()
    existing = conn.execute('SELECT * FROM applications WHERE job_id=? AND journalist_id=?',
                           (job_id, session['user_id'])).fetchone()
    if not existing:
        conn.execute('INSERT INTO applications (job_id, journalist_id, message) VALUES (?,?,?)',
                    (job_id, session['user_id'], message))
        conn.commit()
        flash('تم إرسال طلبك بنجاح!', 'success')
    else:
        flash('لقد تقدمت لهذه الوظيفة مسبقاً', 'warning')
    conn.close()
    return redirect(url_for('job_detail', job_id=job_id))

@app.route('/post-job', methods=['GET', 'POST'])
def post_job():
    if 'user_id' not in session or session['account_type'] != 'channel':
        return redirect(url_for('login'))
    if request.method == 'POST':
        conn = get_db()
        conn.execute('''INSERT INTO jobs (channel_id, title, description, category, location, job_type, salary, requirements)
                       VALUES (?,?,?,?,?,?,?,?)''',
                    (session['user_id'], request.form['title'], request.form['description'],
                     request.form['category'], request.form['location'], request.form['job_type'],
                     request.form.get('salary',''), request.form.get('requirements','')))
        conn.commit()
        conn.close()
        flash('تم نشر الوظيفة بنجاح!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('post_job.html')

@app.route('/profile/<int:user_id>')
def profile(user_id):
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    jobs = None
    if user and user['account_type'] == 'channel':
        jobs = conn.execute('SELECT * FROM jobs WHERE channel_id=? ORDER BY created_at DESC', (user_id,)).fetchall()
    conn.close()
    return render_template('profile.html', user=user, jobs=jobs)

@app.route('/journalists')
def journalists():
    conn = get_db()
    category = request.args.get('category', '')
    query = "SELECT * FROM users WHERE account_type='journalist'"
    params = []
    if category:
        query += ' AND skills LIKE ?'
        params.append(f'%{category}%')
    users = conn.execute(query, params).fetchall()
    conn.close()
    return render_template('journalists.html', journalists=users, category=category)

@app.route('/update-application/<int:app_id>/<status>')
def update_application(app_id, status):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db()
    conn.execute('UPDATE applications SET status=? WHERE id=?', (status, app_id))
    conn.commit()
    conn.close()
    flash('تم تحديث الطلب', 'success')
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    app.run(debug=True, port=5000)
