# ElixirDraw — Render वर Deploy (elixirdraw.com)

## भाग 1 — MySQL database (Render कडे MySQL नाही, बाहेर ठेवावा लागतो)

पर्याय (कोणताही एक निवडा):

| पर्याय | कसं | टीप |
|---|---|---|
| **Aiven MySQL** | aiven.io → Create service → MySQL (Free plan) | SSL लागतं → `DB_SSL=true` ठेवा |
| **TiDB Cloud Serverless** | tidbcloud.com → free cluster (MySQL-compatible) | SSL लागतं |
| **Railway MySQL** | railway.app → New → MySQL | सोपं, थोडे पैसे |
| **Hostinger MySQL** | hPanel → Databases → Remote MySQL (तुमचं Hostinger आधीच आहे) | Render चा IP allow करावा लागतो; Render चे outbound IPs बदलतात — म्हणून "Any host (%)" allow करावं लागेल |

Database तयार झाल्यावर या 5 गोष्टी लिहून ठेवा: **Host, Port, User, Password, Database name**.

### जुना data PythonAnywhere वरून आणणं
1. PythonAnywhere → Databases → Bash console:
   `mysqldump -u elixirdraw -h elixirdraw.mysql.pythonanywhere-services.com 'elixirdraw$rental' > backup.sql`
2. `backup.sql` डाउनलोड करा.
3. नवीन DB मध्ये import:
   `mysql -h <HOST> -P <PORT> -u <USER> -p <DBNAME> < backup.sql`
   (`$` असलेलं जुनं DB नाव dump मध्ये `USE` line असेल तर ती काढून टाका.)
4. App पहिल्यांदा चालल्यावर नवीन columns (plan, top listing, service_providers इ.) **आपोआप** तयार होतात.

## भाग 2 — GitHub वर code ठेवा
1. github.com वर नवीन **private** repo बनवा.
2. या zip मधले सगळे files (app.py, requirements.txt, render.yaml, templates/, static/) push करा.

## भाग 3 — Render वर Web Service
1. render.com → New → **Blueprint** → तुमचं repo निवडा (render.yaml आपोआप वाचला जाईल)
   — किंवा New → Web Service:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app --workers 2 --timeout 120 --bind 0.0.0.0:$PORT`
2. **Environment** मध्ये भरा:

| Key | Value |
|---|---|
| `DB_HOST` | तुमच्या MySQL चा host |
| `DB_PORT` | 3306 (किंवा provider ने दिलेला) |
| `DB_USER` | DB user |
| `DB_PASSWORD` | DB password |
| `DB_NAME` | DB नाव |
| `DB_SSL` | true (Aiven/TiDB साठी) |
| `SECRET_KEY` | कोणतीही लांब random string |
| `MAIL_USERNAME` | elixirdraw26@gmail.com |
| `MAIL_PASSWORD` | Gmail **नवीन** App Password |

3. **Disk (महत्त्वाचं):** Render चा filesystem temporary आहे — redeploy केल्यावर upload केलेले फोटो/documents **नाहीसे होतात**.
   Disk जोडा: Mount path = `/opt/render/project/src/static/uploads`, 1 GB. (Disk साठी paid plan — Starter — लागतो.)
   जुने फोटो PythonAnywhere च्या `static/uploads/` मधून आणून इथे ठेवायचे असतील तर Render Shell/SCP वापरा.

## भाग 4 — elixirdraw.com जोडणं
1. Render → तुमची service → Settings → **Custom Domains** → Add: `www.elixirdraw.com` आणि `elixirdraw.com`
2. Render दाखवेल ते DNS records domain registrar (जिथून domain घेतलाय) मध्ये टाका:
   - `www` → **CNAME** → `<your-service>.onrender.com`
   - `@` (root) → **A record** → Render ने दिलेला IP (किंवा ALIAS/ANAME जर registrar देत असेल)
3. जुने PythonAnywhere चे DNS records काढून टाका.
4. 5–30 मिनिटांनी Render "Verify" करेल आणि **HTTPS (SSL) आपोआप** लागेल.

## महत्त्वाचं — सुरक्षितता
तुमचा जुना code GitHub/zip मध्ये फिरला आहे, त्यात हे उघड होते:
- Gmail App Password (`fevs ausn ...`) → Google Account → Security → App passwords मधून **जुना रद्द करून नवीन** बनवा
- DB password `Shrikant` → नवीन DB साठी वेगळा strong password वापरा
हे आता code मधून काढले आहेत; फक्त Render Environment मध्येच ठेवा. `.env` कधीही GitHub वर टाकू नका.

## Free plan बद्दल
Free web service 15 मिनिटं निष्क्रिय राहिल्यावर झोपते (पहिली request ~50 सेकंद घेते) आणि Disk मिळत नाही → uploads टिकत नाहीत. खऱ्या वापरासाठी **Starter ($7/महिना) + Disk** घ्या.
