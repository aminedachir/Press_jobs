from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import os
import subprocess
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'pressjobs_secret_2024'

DB_PATH = 'pressjobs.db'
UPLOAD_FOLDER = os.path.join('static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def convert_to_mp4(src_path):
    """Convert any video to H.264 MP4 for universal browser support.
    Returns the new filename (without folder), or original name if conversion fails."""
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
            os.remove(src_path)          # delete original
            return out_path
    except Exception:
        pass
    return src_path                      # fallback: keep original

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

# ── Migrate: add new journalist profile columns if they don't exist ──
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
    if 'user_id' in session:
        return redirect(url_for('index'))
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

    # ── Basic info ──
    name              = request.form.get('name', '').strip()
    last_name         = request.form.get('last_name', '').strip()
    location          = request.form.get('location', '').strip()
    bio               = request.form.get('bio', '').strip()
    gender            = request.form.get('gender', '').strip()
    civil_status      = request.form.get('civil_status', '').strip()
    # ── Experience ──
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
        # Convert video to mp4 for browser compatibility
        if ext in ALL_VIDEO and ext != '.mp4':
            converted = convert_to_mp4(full_path)
            uname = os.path.basename(converted)
        return uname

    cv_filename  = save_file('cv_file',    'cv',    {'.pdf', '.doc', '.docx'})
    intro_video  = save_file('intro_video','introv', ALL_VIDEO)

    conn = get_db()
    old = conn.execute('SELECT cv_filename, intro_video FROM users WHERE id=?', (user_id,)).fetchone()

    # Delete old files if replaced
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

    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    media_filename = None
    media_type = None

    VIDEO_EXT = {'.mp4','.webm','.mov','.avi','.mkv','.flv','.wmv','.m4v','.3gp','.ogv','.ts','.mts','.m2ts'}
    IMAGE_EXT = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff','.tif','.heic','.heif','.avif','.jfif','.svg'}

    if 'media' in request.files:
        file = request.files['media']
        if file and file.filename:
            filename = secure_filename(file.filename)
            ext = os.path.splitext(filename)[1].lower()
            unique_name = f"post_{user_id}_{int(datetime.utcnow().timestamp())}{ext}"
            file_path = os.path.join(UPLOAD_FOLDER, unique_name)
            file.save(file_path)
            if ext in VIDEO_EXT:
                media_type = 'video'
                # Convert to mp4 for universal browser support
                converted = convert_to_mp4(file_path)
                media_filename = os.path.basename(converted)
            elif ext in IMAGE_EXT:
                media_type = 'image'
                media_filename = unique_name
            else:
                media_type = 'other'
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

@app.route('/channels')
def channels():
    conn = get_db()
    search = request.args.get('search', '')
    query = "SELECT * FROM users WHERE account_type='channel'"
    params = []
    if search:
        query += ' AND (name LIKE ? OR location LIKE ? OR bio LIKE ?)'
        params += [f'%{search}%', f'%{search}%', f'%{search}%']
    query += ' ORDER BY created_at DESC'
    all_channels = conn.execute(query, params).fetchall()
    # Get job count per channel
    job_counts = {}
    for ch in all_channels:
        count = conn.execute('SELECT COUNT(*) FROM jobs WHERE channel_id=?', (ch['id'],)).fetchone()[0]
        job_counts[ch['id']] = count
    conn.close()
    return render_template('channels.html', channels=all_channels, job_counts=job_counts, search=search)

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
    flash('تم تحديث حالة الطلب', 'success')
    return redirect(url_for('profile', user_id=session['user_id']))

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)