from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import os
import subprocess
import threading
import uuid
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'pressjobs_secret_2024')

_DATA_DIR = os.environ.get('DATA_DIR', '.')
DB_PATH = os.path.join(_DATA_DIR, 'pressjobs.db')
UPLOAD_FOLDER = os.path.join(_DATA_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

os.makedirs(os.path.join('static', 'uploads'), exist_ok=True)

_bg_jobs      = {}
_bg_jobs_lock = threading.Lock()

def convert_to_mp4(src_path):
    base = os.path.splitext(src_path)[0]
    out_path = base + '_c.mp4'
    try:
        result = subprocess.run([
            'ffmpeg', '-y', '-i', src_path,
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart',
            out_path
        ], capture_output=True, timeout=300)
        if result.returncode == 0 and os.path.exists(out_path):
            os.remove(src_path)
            return out_path
    except Exception:
        pass
    return src_path

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
    c.execute('''CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT,
        description TEXT,
        media_filename TEXT,
        media_type TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')
    conn.commit()
    conn.close()

init_db()

def migrate_db():
    conn = get_db()
    c = conn.cursor()
    existing = [row[1] for row in c.execute("PRAGMA table_info(users)").fetchall()]
    new_cols = [
        ("last_name",          "TEXT"),
        ("gender",             "TEXT"),
        ("civil_status",       "TEXT"),
        ("specialty",          "TEXT"),
        ("years_experience",   "TEXT"),
        ("preferred_channels", "TEXT"),
        ("extra_skills",       "TEXT"),
        ("cv_filename",        "TEXT"),
        ("intro_video",        "TEXT"),
        ("article_links",      "TEXT"),
        ("channel_type",       "TEXT"),   
        ("cover_image",        "TEXT"),   
    ]
    for col, coltype in new_cols:
        if col not in existing:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {coltype}")
    existing_posts = [row[1] for row in c.execute("PRAGMA table_info(posts)").fetchall()]
    if "post_type" not in existing_posts:
        c.execute("ALTER TABLE posts ADD COLUMN post_type TEXT DEFAULT 'media'")
    conn.commit()
    conn.close()

migrate_db()

from flask import send_from_directory

@app.route('/static/uploads/<path:filename>')
def uploaded_file(filename):
    """Serve uploads from DATA_DIR (persistent disk on Render, or static/uploads locally)."""
    
    data_uploads = os.path.join(_DATA_DIR, 'uploads')
    if os.path.exists(os.path.join(data_uploads, filename)):
        return send_from_directory(data_uploads, filename)
   
    return send_from_directory(os.path.join('static', 'uploads'), filename)

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('profile', user_id=session['user_id']))
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
    if 'user_id' in session:
        return redirect(url_for('profile', user_id=session['user_id']))
    if request.method == 'POST':
        name         = request.form['name']
        email        = request.form['email']
        password     = generate_password_hash(request.form['password'])
        account_type = request.form['account_type']
        location     = request.form.get('location', '')
        bio          = request.form.get('bio', '')
        skills       = request.form.get('skills', '')
        education    = request.form.get('education', '')
        channel_type = request.form.get('channel_type', '')   
        try:
            conn = get_db()
            conn.execute('''INSERT INTO users (name, email, password, account_type, location, bio, skills, education, channel_type)
                           VALUES (?,?,?,?,?,?,?,?,?)''',
                        (name, email, password, account_type, location, bio, skills, education, channel_type))
            conn.commit()
            conn.close()
            flash('تم إنشاء حسابك بنجاح! يمكنك تسجيل الدخول الآن', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('البريد الإلكتروني مستخدم بالفعل', 'error')
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email    = request.form['email']
        password = request.form['password']
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            session['user_id']    = user['id']
            session['user_name']  = user['name']
            session['account_type'] = user['account_type']
            flash(f'مرحباً {user["name"]}!', 'success')
            return redirect(url_for('profile', user_id=user['id']))
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
    return redirect(url_for('profile', user_id=session['user_id']))

@app.route('/job/<int:job_id>/edit', methods=['POST'])
def edit_job(job_id):
    if 'user_id' not in session or session.get('account_type') != 'channel':
        return redirect(url_for('login'))
    conn = get_db()
    job = conn.execute('SELECT * FROM jobs WHERE id=? AND channel_id=?', (job_id, session['user_id'])).fetchone()
    if not job:
        conn.close()
        flash('غير مصرح لك بتعديل هذه الوظيفة', 'error')
        return redirect(url_for('profile', user_id=session['user_id']))
    conn.execute('''UPDATE jobs SET title=?, description=?, category=?, location=?, job_type=?, salary=?, requirements=?
                    WHERE id=?''',
                 (request.form['title'], request.form['description'], request.form['category'],
                  request.form.get('location',''), request.form.get('job_type',''),
                  request.form.get('salary',''), request.form.get('requirements',''), job_id))
    conn.commit()
    conn.close()
    flash('تم تعديل الوظيفة بنجاح!', 'success')
    return redirect(url_for('profile', user_id=session['user_id']))

@app.route('/job/<int:job_id>/delete')
def delete_job(job_id):
    if 'user_id' not in session or session.get('account_type') != 'channel':
        return redirect(url_for('login'))
    conn = get_db()
    job = conn.execute('SELECT * FROM jobs WHERE id=? AND channel_id=?', (job_id, session['user_id'])).fetchone()
    if job:
        conn.execute('DELETE FROM applications WHERE job_id=?', (job_id,))
        conn.execute('DELETE FROM jobs WHERE id=?', (job_id,))
        conn.commit()
        flash('تم حذف الوظيفة نهائياً', 'success')
    conn.close()
    return redirect(url_for('profile', user_id=session['user_id']))

@app.route('/jobs')
def jobs():
    category = request.args.get('category', '')
    search   = request.args.get('search', '')
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
    if not user:
        conn.close()
        flash('المستخدم غير موجود', 'error')
        return redirect(url_for('index'))

    if user['account_type'] == 'channel':
        jobs = conn.execute(
            'SELECT * FROM jobs WHERE channel_id=? ORDER BY created_at DESC', (user_id,)
        ).fetchall()
        applications = conn.execute('''
            SELECT applications.*, jobs.title AS job_title,
                   u.name AS applicant_name, u.profile_image AS applicant_image,
                   u.skills, u.education, u.location AS applicant_location,
                   u.years_experience, u.specialty, u.cv_filename
            FROM applications
            JOIN jobs ON applications.job_id = jobs.id
            JOIN users u ON applications.journalist_id = u.id
            WHERE jobs.channel_id = ?
            ORDER BY applications.created_at DESC
        ''', (user_id,)).fetchall()
        total_applications = len(applications)
        accepted_count = sum(1 for a in applications if a['status'] == 'accepted')
        job_categories = list({j['category'] for j in jobs})
        conn.close()
        return render_template('channel_profile.html',
            channel=user, jobs=jobs, applications=applications,
            total_applications=total_applications, accepted_count=accepted_count,
            job_categories=job_categories)

    posts = conn.execute(
        'SELECT * FROM posts WHERE user_id=? ORDER BY created_at DESC', (user_id,)
    ).fetchall()
    conn.close()
    return render_template('profile.html', user=user, jobs=None, posts=posts)

@app.route('/profile/<int:user_id>/update', methods=['POST'])
def update_profile(user_id):
    if 'user_id' not in session or session['user_id'] != user_id:
        return redirect(url_for('login'))

    name              = request.form.get('name', '').strip()
    last_name         = request.form.get('last_name', '').strip()
    location          = request.form.get('location', '').strip()
    bio               = request.form.get('bio', '').strip()
    gender            = request.form.get('gender', '').strip()
    civil_status      = request.form.get('civil_status', '').strip()
    specialty         = request.form.get('specialty', '').strip()
    years_experience  = request.form.get('years_experience', '').strip()
    education         = request.form.get('education', '').strip()
    skills            = request.form.get('skills', '').strip()
    preferred_channels= request.form.get('preferred_channels', '').strip()
    extra_skills      = request.form.get('extra_skills', '').strip()
    article_links     = request.form.get('article_links', '').strip()

    ALL_VIDEO = {'.mp4','.webm','.mov','.avi','.mkv','.flv','.wmv','.m4v','.3gp','.ogv','.ts','.mts','.m2ts'}
    IMAGE_EXT = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff','.tif','.heic','.heif','.avif','.jfif'}

    def save_file(field, prefix, allowed):
        if field not in request.files:
            return None
        f = request.files[field]
        if not f or not f.filename:
            return None
        ext = os.path.splitext(secure_filename(f.filename))[1].lower()
        if ext not in allowed:
            return None
        uname = f"{prefix}_{user_id}_{int(datetime.utcnow().timestamp())}{ext}"
        full_path = os.path.join(UPLOAD_FOLDER, uname)
        f.save(full_path)
        if ext in ALL_VIDEO and ext != '.mp4':
            converted = convert_to_mp4(full_path)
            uname = os.path.basename(converted)
        return uname

    cv_filename  = save_file('cv_file',    'cv',    {'.pdf', '.doc', '.docx'})
    intro_video  = save_file('intro_video','introv', ALL_VIDEO)

    conn = get_db()
    old = conn.execute('SELECT cv_filename, intro_video FROM users WHERE id=?', (user_id,)).fetchone()

    for field, new_val in [('cv_filename', cv_filename), ('intro_video', intro_video)]:
        if new_val and old and old[field]:
            old_path = os.path.join(UPLOAD_FOLDER, old[field])
            if os.path.exists(old_path):
                os.remove(old_path)

    conn.execute('''UPDATE users SET
        name=?, last_name=?, location=?, bio=?, gender=?, civil_status=?,
        specialty=?, years_experience=?, education=?, skills=?,
        preferred_channels=?, extra_skills=?, article_links=?
        {cv} {iv}
        WHERE id=?'''.format(
            cv=', cv_filename=?' if cv_filename else '',
            iv=', intro_video=?' if intro_video else ''
        ),
        [name, last_name, location, bio, gender, civil_status,
         specialty, years_experience, education, skills,
         preferred_channels, extra_skills, article_links]
        + ([cv_filename] if cv_filename else [])
        + ([intro_video] if intro_video else [])
        + [user_id]
    )
    conn.commit()
    conn.close()

    session['user_name'] = name
    flash('تم تحديث معلومات الملف الشخصي بنجاح!', 'success')
    return redirect(url_for('profile', user_id=user_id))

@app.route('/profile/<int:user_id>/upload-picture', methods=['POST'])
def upload_picture(user_id):
    if 'user_id' not in session or session['user_id'] != user_id:
        return redirect(url_for('login'))
    if 'profile_image' not in request.files:
        flash('لم يتم اختيار أي صورة', 'error')
        return redirect(url_for('profile', user_id=user_id))
    file = request.files['profile_image']
    if not file or not file.filename:
        flash('لم يتم اختيار أي صورة', 'error')
        return redirect(url_for('profile', user_id=user_id))
    IMAGE_EXT = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff','.tif','.heic','.heif','.avif','.jfif'}
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in IMAGE_EXT:
        flash('صيغة الصورة غير مدعومة', 'error')
        return redirect(url_for('profile', user_id=user_id))
    conn = get_db()
    old = conn.execute('SELECT profile_image FROM users WHERE id=?', (user_id,)).fetchone()
    if old and old['profile_image']:
        old_path = os.path.join(UPLOAD_FOLDER, old['profile_image'])
        if os.path.exists(old_path):
            os.remove(old_path)
    unique_name = f"avatar_{user_id}_{int(datetime.utcnow().timestamp())}{ext}"
    file.save(os.path.join(UPLOAD_FOLDER, unique_name))
    conn.execute('UPDATE users SET profile_image=? WHERE id=?', (unique_name, user_id))
    conn.commit()
    conn.close()
    flash('تم تحديث صورة الملف الشخصي بنجاح!', 'success')
    return redirect(url_for('profile', user_id=user_id))

@app.route('/profile/<int:user_id>/post', methods=['POST'])
def create_post(user_id):
    if 'user_id' not in session or session['user_id'] != user_id:
        return redirect(url_for('login'))

    title       = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    media_filename = None
    media_type     = None

    VIDEO_EXT = {'.mp4','.webm','.mov','.avi','.mkv','.flv','.wmv','.m4v','.3gp','.ogv','.ts','.mts','.m2ts'}
    IMAGE_EXT = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff','.tif','.heic','.heif','.avif','.jfif','.svg'}

    processed_video = request.form.get('processed_video', '').strip()
    if processed_video:
        safe_name = secure_filename(processed_video)
        if os.path.exists(os.path.join(UPLOAD_FOLDER, safe_name)):
            media_filename = safe_name
            media_type     = 'video'

    if not media_filename and 'media' in request.files:
        file = request.files['media']
        if file and file.filename:
            filename    = secure_filename(file.filename)
            ext         = os.path.splitext(filename)[1].lower()
            unique_name = f"post_{user_id}_{int(datetime.utcnow().timestamp())}{ext}"
            file_path   = os.path.join(UPLOAD_FOLDER, unique_name)
            file.save(file_path)
            if ext in VIDEO_EXT:
                media_type = 'video'
                converted  = convert_to_mp4(file_path)
                media_filename = os.path.basename(converted)
            elif ext in IMAGE_EXT:
                media_type     = 'image'
                media_filename = unique_name
            else:
                media_type     = 'other'
                media_filename = unique_name

    conn = get_db()
    conn.execute('''INSERT INTO posts (user_id, title, description, media_filename, media_type)
                    VALUES (?, ?, ?, ?, ?)''',
                 (user_id, title, description, media_filename, media_type))
    conn.commit()
    conn.close()
    flash('تم نشر المحتوى بنجاح!', 'success')
    return redirect(url_for('profile', user_id=user_id))

@app.route('/channel/<int:channel_id>/update', methods=['POST'])
def update_channel(channel_id):
    if 'user_id' not in session or session['user_id'] != channel_id:
        return redirect(url_for('login'))
    name     = request.form.get('name', '').strip()
    location = request.form.get('location', '').strip()
    bio      = request.form.get('bio', '').strip()
    conn = get_db()
    conn.execute('UPDATE users SET name=?, location=?, bio=? WHERE id=?',
                 (name, location, bio, channel_id))
    conn.commit()
    conn.close()
    session['user_name'] = name
    flash('تم تحديث معلومات القناة بنجاح!', 'success')
    return redirect(url_for('profile', user_id=channel_id))

@app.route('/channel/<int:channel_id>/upload-picture', methods=['POST'])
def upload_channel_picture(channel_id):
    if 'user_id' not in session or session['user_id'] != channel_id:
        return redirect(url_for('login'))
    if 'profile_image' not in request.files:
        flash('لم يتم اختيار أي صورة', 'error')
        return redirect(url_for('profile', user_id=channel_id))
    file = request.files['profile_image']
    if not file or not file.filename:
        flash('لم يتم اختيار أي صورة', 'error')
        return redirect(url_for('profile', user_id=channel_id))
    IMAGE_EXT = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff','.tif','.heic','.heif','.avif','.jfif'}
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in IMAGE_EXT:
        flash('صيغة الصورة غير مدعومة', 'error')
        return redirect(url_for('profile', user_id=channel_id))
    conn = get_db()
    old = conn.execute('SELECT profile_image FROM users WHERE id=?', (channel_id,)).fetchone()
    if old and old['profile_image']:
        old_path = os.path.join(UPLOAD_FOLDER, old['profile_image'])
        if os.path.exists(old_path):
            os.remove(old_path)
    unique_name = f"avatar_{channel_id}_{int(datetime.utcnow().timestamp())}{ext}"
    file.save(os.path.join(UPLOAD_FOLDER, unique_name))
    conn.execute('UPDATE users SET profile_image=? WHERE id=?', (unique_name, channel_id))
    conn.commit()
    conn.close()
    flash('تم تحديث شعار القناة بنجاح!', 'success')
    return redirect(url_for('profile', user_id=channel_id))

@app.route('/channel/<int:channel_id>/upload-cover', methods=['POST'])
def upload_channel_cover(channel_id):
    if 'user_id' not in session or session['user_id'] != channel_id:
        return redirect(url_for('login'))
    if 'cover_image' not in request.files:
        flash('لم يتم اختيار أي صورة', 'error')
        return redirect(url_for('profile', user_id=channel_id))
    file = request.files['cover_image']
    if not file or not file.filename:
        flash('لم يتم اختيار أي صورة', 'error')
        return redirect(url_for('profile', user_id=channel_id))
    IMAGE_EXT = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff','.tif','.heic','.heif','.avif','.jfif'}
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in IMAGE_EXT:
        flash('صيغة الصورة غير مدعومة', 'error')
        return redirect(url_for('profile', user_id=channel_id))
    conn = get_db()
    old = conn.execute('SELECT cover_image FROM users WHERE id=?', (channel_id,)).fetchone()
    if old and old['cover_image']:
        old_path = os.path.join(UPLOAD_FOLDER, old['cover_image'])
        if os.path.exists(old_path):
            os.remove(old_path)
    unique_name = f"cover_{channel_id}_{int(datetime.utcnow().timestamp())}{ext}"
    file.save(os.path.join(UPLOAD_FOLDER, unique_name))
    conn.execute('UPDATE users SET cover_image=? WHERE id=?', (unique_name, channel_id))
    conn.commit()
    conn.close()
    flash('تم تحديث صورة الغلاف بنجاح!', 'success')
    return redirect(url_for('profile', user_id=channel_id))

@app.route('/channels')
def channels():
    conn = get_db()
    search       = request.args.get('search', '')
    channel_type = request.args.get('type', '')  
    query  = "SELECT * FROM users WHERE account_type='channel'"
    params = []
    if channel_type:
        query += ' AND channel_type=?'
        params.append(channel_type)
    if search:
        query += ' AND (name LIKE ? OR location LIKE ? OR bio LIKE ?)'
        params += [f'%{search}%', f'%{search}%', f'%{search}%']
    query += ' ORDER BY created_at DESC'
    all_channels = conn.execute(query, params).fetchall()
    job_counts = {}
    for ch in all_channels:
        count = conn.execute('SELECT COUNT(*) FROM jobs WHERE channel_id=?', (ch['id'],)).fetchone()[0]
        job_counts[ch['id']] = count
    conn.close()
    return render_template('channels.html', channels=all_channels, job_counts=job_counts,
                           search=search, channel_type=channel_type)

@app.route('/journalists')
def journalists():
    conn = get_db()
    category = request.args.get('category', '')
    search   = request.args.get('search', '')
    query  = "SELECT * FROM users WHERE account_type='journalist'"
    params = []
    if category:
        query += ' AND specialty LIKE ?'
        params.append(f'%{category}%')
    if search:
        query += ' AND (name LIKE ? OR skills LIKE ? OR location LIKE ?)'
        params += [f'%{search}%', f'%{search}%', f'%{search}%']
    query += ' ORDER BY created_at DESC'
    users = conn.execute(query, params).fetchall()
    conn.close()
    return render_template('journalists.html', journalists=users, category=category, search=search)

@app.route('/update-application/<int:app_id>/<status>')
def update_application(app_id, status):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db()
    conn.execute('UPDATE applications SET status=? WHERE id=?', (status, app_id))
    conn.commit()
    conn.close()
    flash('تم تحديث حالة الطلب', 'success')
    return redirect(url_for('profile', user_id=session['user_id']))



def migrate_admin():
    conn = get_db()
    c = conn.cursor()
    cols = [row[1] for row in c.execute("PRAGMA table_info(users)").fetchall()]
    if 'is_admin' not in cols:
        c.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
        conn.commit()
    conn.close()

migrate_admin()

ADMIN_EMAIL    = os.environ.get('ADMIN_EMAIL', 'admin@pressjobs.dz')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'Admin@2024!')

def ensure_admin():
    conn = get_db()
    exists = conn.execute('SELECT id FROM users WHERE email=?', (ADMIN_EMAIL,)).fetchone()
    if not exists:
        conn.execute(
            '''INSERT INTO users (name, email, password, account_type, is_admin)
               VALUES (?,?,?,?,1)''',
            ('مدير النظام', ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD), 'admin')
        )
        conn.commit()
    else:
        conn.execute('UPDATE users SET is_admin=1 WHERE email=?', (ADMIN_EMAIL,))
        conn.commit()
    conn.close()

ensure_admin()

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session or not session.get('is_admin'):
            flash('غير مصرح لك بالدخول إلى لوحة التحكم', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated




@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        email    = request.form['email']
        password = request.form['password']
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE email=? AND is_admin=1', (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            session['user_id']   = user['id']
            session['user_name'] = user['name']
            session['is_admin']  = True
            flash('مرحباً بك في لوحة التحكم', 'success')
            return redirect(url_for('admin_dashboard'))
        flash('بيانات الدخول غير صحيحة أو ليس لديك صلاحية الوصول', 'error')
    return render_template('admin_login.html')



@app.route('/admin')
@admin_required
def admin_dashboard():
    conn = get_db()
    total_journalists = conn.execute("SELECT COUNT(*) FROM users WHERE account_type='journalist'").fetchone()[0]
    total_channels    = conn.execute("SELECT COUNT(*) FROM users WHERE account_type='channel'").fetchone()[0]
    total_jobs        = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    total_apps        = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]

    journalists = conn.execute(
        "SELECT * FROM users WHERE account_type='journalist' ORDER BY created_at DESC"
    ).fetchall()
    channels = conn.execute(
        "SELECT * FROM users WHERE account_type='channel' ORDER BY created_at DESC"
    ).fetchall()

    job_counts = {}
    for ch in channels:
        job_counts[ch['id']] = conn.execute(
            'SELECT COUNT(*) FROM jobs WHERE channel_id=?', (ch['id'],)
        ).fetchone()[0]

    app_counts = {}
    for j in journalists:
        app_counts[j['id']] = conn.execute(
            'SELECT COUNT(*) FROM applications WHERE journalist_id=?', (j['id'],)
        ).fetchone()[0]

    recent_jobs = conn.execute(
        '''SELECT jobs.*, users.name as channel_name
           FROM jobs JOIN users ON jobs.channel_id = users.id
           ORDER BY jobs.created_at DESC LIMIT 10'''
    ).fetchall()

    conn.close()
    return render_template('admin_dashboard.html',
        total_journalists=total_journalists,
        total_channels=total_channels,
        total_jobs=total_jobs,
        total_apps=total_apps,
        journalists=journalists,
        channels=channels,
        job_counts=job_counts,
        app_counts=app_counts,
        recent_jobs=recent_jobs,
    )



@app.route('/admin/delete/journalist/<int:user_id>', methods=['POST'])
@admin_required
def admin_delete_journalist(user_id):
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id=? AND account_type=?',
                        (user_id, 'journalist')).fetchone()
    if not user:
        flash('الصحفي غير موجود', 'error')
        conn.close()
        return redirect(url_for('admin_dashboard'))

    for field in ('profile_image', 'cv_filename', 'intro_video'):
        fname = user[field] if field in user.keys() else None
        if fname:
            fpath = os.path.join(UPLOAD_FOLDER, fname)
            if os.path.exists(fpath):
                os.remove(fpath)

    posts = conn.execute('SELECT media_filename FROM posts WHERE user_id=?', (user_id,)).fetchall()
    for p in posts:
        if p['media_filename']:
            fpath = os.path.join(UPLOAD_FOLDER, p['media_filename'])
            if os.path.exists(fpath):
                os.remove(fpath)

    conn.execute('DELETE FROM applications WHERE journalist_id=?', (user_id,))
    conn.execute('DELETE FROM posts WHERE user_id=?', (user_id,))
    conn.execute('DELETE FROM users WHERE id=?', (user_id,))
    conn.commit()
    conn.close()
    flash(f'تم حذف حساب الصحفي "{user["name"]}" بنجاح', 'success')
    return redirect(url_for('admin_dashboard') + '#journalists')



@app.route('/admin/delete/channel/<int:channel_id>', methods=['POST'])
@admin_required
def admin_delete_channel(channel_id):
    conn = get_db()
    channel = conn.execute('SELECT * FROM users WHERE id=? AND account_type=?',
                           (channel_id, 'channel')).fetchone()
    if not channel:
        flash('القناة غير موجودة', 'error')
        conn.close()
        return redirect(url_for('admin_dashboard'))

    if channel['profile_image']:
        fpath = os.path.join(UPLOAD_FOLDER, channel['profile_image'])
        if os.path.exists(fpath):
            os.remove(fpath)

    jobs = conn.execute('SELECT id FROM jobs WHERE channel_id=?', (channel_id,)).fetchall()
    for job in jobs:
        conn.execute('DELETE FROM applications WHERE job_id=?', (job['id'],))
    conn.execute('DELETE FROM jobs WHERE channel_id=?', (channel_id,))
    conn.execute('DELETE FROM users WHERE id=?', (channel_id,))
    conn.commit()
    conn.close()
    flash(f'تم حذف حساب القناة "{channel["name"]}" وجميع وظائفها بنجاح', 'success')
    return redirect(url_for('admin_dashboard') + '#channels')



@app.route('/admin/delete/job/<int:job_id>', methods=['POST'])
@admin_required
def admin_delete_job(job_id):
    conn = get_db()
    conn.execute('DELETE FROM applications WHERE job_id=?', (job_id,))
    conn.execute('DELETE FROM jobs WHERE id=?', (job_id,))
    conn.commit()
    conn.close()
    flash('تم حذف الوظيفة بنجاح', 'success')
    return redirect(url_for('admin_dashboard') + '#jobs')



@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))



@app.route('/profile/<int:user_id>/process-intro-bg', methods=['POST'])
def process_intro_bg(user_id):
    """Start BG replacement for the intro video (edit-profile tab)."""
    if 'user_id' not in session or session['user_id'] != user_id:
        return jsonify({'error': 'غير مصرح'}), 401
    return _handle_process_bg()


@app.route('/profile/<int:user_id>/confirm-intro-bg', methods=['POST'])
def confirm_intro_bg(user_id):
    """After processing is done, save the result as the user's intro_video."""
    if 'user_id' not in session or session['user_id'] != user_id:
        return jsonify({'error': 'غير مصرح'}), 401

    filename = request.json.get('filename', '').strip()
    if not filename:
        return jsonify({'error': 'اسم الملف مفقود'}), 400

    safe = secure_filename(filename)
    if not os.path.exists(os.path.join(UPLOAD_FOLDER, safe)):
        return jsonify({'error': 'الملف المعالج غير موجود'}), 404

    conn = get_db()
    old = conn.execute('SELECT intro_video FROM users WHERE id=?', (user_id,)).fetchone()
    if old and old['intro_video']:
        old_path = os.path.join(UPLOAD_FOLDER, old['intro_video'])
        if os.path.exists(old_path):
            try: os.remove(old_path)
            except Exception: pass
    conn.execute('UPDATE users SET intro_video=? WHERE id=?', (safe, user_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'filename': safe})


@app.route('/api/process-bg', methods=['POST'])
def api_process_bg():
    """Start BG replacement for a post video."""
    if 'user_id' not in session:
        return jsonify({'error': 'غير مصرح'}), 401
    return _handle_process_bg()


def _handle_process_bg():
    """
    Shared logic: receive video + studio_image, launch thread, return task_id.
    """
    user_id   = session['user_id']
    IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp'}
    ALL_VIDEO = {'.mp4', '.webm', '.mov', '.avi', '.mkv', '.flv', '.wmv', '.m4v', '.3gp', '.ogv'}

    studio_file = request.files.get('studio_image')
    if not studio_file or not studio_file.filename:
        return jsonify({'error': 'الرجاء رفع صورة الخلفية'}), 400
    sext = os.path.splitext(secure_filename(studio_file.filename))[1].lower()
    if sext not in IMAGE_EXT:
        return jsonify({'error': 'صيغة صورة الخلفية غير مدعومة (JPG/PNG/WebP فقط)'}), 400

    ts          = int(datetime.utcnow().timestamp())
    studio_name = f"studio_tmp_{user_id}_{ts}{sext}"
    studio_path = os.path.join(UPLOAD_FOLDER, studio_name)
    studio_file.save(studio_path)

    video_file = request.files.get('video')
    if not video_file or not video_file.filename:
        return jsonify({'error': 'الرجاء اختيار فيديو'}), 400
    vext = os.path.splitext(secure_filename(video_file.filename))[1].lower()
    if vext not in ALL_VIDEO:
        return jsonify({'error': 'صيغة الفيديو غير مدعومة'}), 400

    task_id      = str(uuid.uuid4())
    fg_filename  = f"fg_{user_id}_{ts}{vext}"
    out_filename = f"bgout_{user_id}_{ts}.mp4"
    fg_path      = os.path.join(UPLOAD_FOLDER, fg_filename)
    out_path     = os.path.join(UPLOAD_FOLDER, out_filename)
    video_file.save(fg_path)

    with _bg_jobs_lock:
        _bg_jobs[task_id] = {
            'status':     'queued',
            'progress':   0,
            'result':     None,
            'fg':         fg_filename,
            'studio_tmp': studio_name,
        }

    threading.Thread(
        target=_run_bg_replacement,
        args=(task_id, fg_path, studio_path, out_path),
        daemon=True
    ).start()
    return jsonify({'task_id': task_id})


@app.route('/api/process-bg/status/<task_id>')
def api_process_bg_status(task_id):
    """Poll processing status."""
    with _bg_jobs_lock:
        job = dict(_bg_jobs.get(task_id, {}))
    if not job:
        return jsonify({'error': 'مهمة غير موجودة'}), 404
    return jsonify(job)


def _run_bg_replacement(task_id, fg_path, studio_path, output_path):
    """
    MediaPipe selfie segmentation background replacement.

    Correct blending in float32 [0-1]:
        output = fg * mask + bg * (1 - mask)

    Converting to float32 before multiplying avoids uint8 overflow.
    Audio is restored from the original via ffmpeg.
    """
    try:
        with _bg_jobs_lock:
            _bg_jobs[task_id]['status'] = 'processing'

        import cv2
        import numpy as np
        import mediapipe as mp

        fg_cap = cv2.VideoCapture(fg_path)
        if not fg_cap.isOpened():
            raise RuntimeError(f"Cannot open video: {fg_path}")

        total_frames = int(fg_cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        width        = int(fg_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height       = int(fg_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps          = fg_cap.get(cv2.CAP_PROP_FPS) or 25.0

        bg_img = cv2.imread(studio_path)
        if bg_img is None:
            raise FileNotFoundError(f"Studio image not found: {studio_path}")
        bg_img = cv2.resize(bg_img, (width, height))
        bg_f32 = bg_img.astype(np.float32) / 255.0

        raw_path = output_path.replace('.mp4', '_raw.mp4')
        writer   = cv2.VideoWriter(raw_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (width, height))
        if not writer.isOpened():
            raise RuntimeError("VideoWriter could not be opened")

        segmenter = mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1)

        frame_idx = 0
        while True:
            ret, frame = fg_cap.read()
            if not ret:
                break
            rgb      = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mask     = segmenter.process(rgb).segmentation_mask
            mask     = cv2.GaussianBlur(mask, (21, 21), 0)
            mask_3ch = np.stack([mask] * 3, axis=-1)
            fg_f32   = frame.astype(np.float32) / 255.0
            blended  = fg_f32 * mask_3ch + bg_f32 * (1.0 - mask_3ch)
            writer.write(np.clip(blended * 255.0, 0, 255).astype(np.uint8))
            frame_idx += 1
            with _bg_jobs_lock:
                _bg_jobs[task_id]['progress'] = min(88, int(frame_idx / total_frames * 88))

        fg_cap.release()
        writer.release()
        segmenter.close()

        with _bg_jobs_lock:
            _bg_jobs[task_id]['progress'] = 90

        result = subprocess.run([
            'ffmpeg', '-y',
            '-i', raw_path,
            '-i', fg_path,
            '-map', '0:v:0',
            '-map', '1:a?',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '22',
            '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart',
            output_path
        ], capture_output=True, timeout=600)

        if os.path.exists(raw_path):
            os.remove(raw_path)

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg error: {result.stderr.decode()}")

        with _bg_jobs_lock:
            _bg_jobs[task_id]['status']   = 'done'
            _bg_jobs[task_id]['progress'] = 100
            _bg_jobs[task_id]['result']   = os.path.basename(output_path)

    except Exception as e:
        for p in [output_path, output_path.replace('.mp4', '_raw.mp4')]:
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
        with _bg_jobs_lock:
            _bg_jobs[task_id]['status'] = 'error'
            _bg_jobs[task_id]['error']  = str(e)
    finally:
        with _bg_jobs_lock:
            studio_tmp = _bg_jobs.get(task_id, {}).get('studio_tmp')
        if studio_tmp:
            p = os.path.join(UPLOAD_FOLDER, studio_tmp)
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)