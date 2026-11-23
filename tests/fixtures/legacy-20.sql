CREATE TABLE artifacts(turn_id INTEGER,case_id TEXT,name TEXT,content TEXT,sha256 TEXT,bytes INTEGER,PRIMARY KEY(turn_id,name));
INSERT INTO "artifacts" VALUES(1,'47369fa25c56481e','main.py','def add(a, b):
    return a + b
','ba1a531f581d2e6094e978ed6f7aca7a8d92eeb62c6e7ad73ee692f7f18bc772',32);
CREATE TABLE case_policy(case_id TEXT PRIMARY KEY, mode TEXT NOT NULL);
INSERT INTO "case_policy" VALUES('47369fa25c56481e','auto');
CREATE TABLE cases(id TEXT PRIMARY KEY,title TEXT NOT NULL,brief TEXT NOT NULL,team TEXT NOT NULL DEFAULT '',pinned TEXT NOT NULL DEFAULT '',door TEXT NOT NULL DEFAULT 'auto',door_pin TEXT NOT NULL DEFAULT 'auto',consult_team TEXT NOT NULL DEFAULT '',hold_kind TEXT NOT NULL DEFAULT '',model TEXT NOT NULL,provider TEXT NOT NULL,web INTEGER NOT NULL,stage TEXT NOT NULL DEFAULT 'route',status TEXT NOT NULL DEFAULT 'ready',epoch INTEGER NOT NULL DEFAULT 0,turns INTEGER NOT NULL DEFAULT 0,reason TEXT NOT NULL DEFAULT '',created TEXT,updated TEXT);
INSERT INTO "cases" VALUES('47369fa25c56481e','Hồ sơ 2.0 thực','Viết hàm cộng hai số nguyên Python','eng','','auto','auto','','','fixture-legacy','ollama',0,'sign','done',0,0,'','2026-11-20T14:43:57.046+00:00','2026-11-20T14:43:57.046+00:00');
CREATE TABLE checkpoints(case_id TEXT,epoch INTEGER,kind TEXT,body TEXT,PRIMARY KEY(case_id,epoch,kind));
INSERT INTO "checkpoints" VALUES('47369fa25c56481e',0,'files','{"signature":"2.0","parts":{"main.py:0":"def add(a, b):\n    return a + b\n"}}');
CREATE TABLE events(id INTEGER PRIMARY KEY,case_id TEXT,kind TEXT,stage TEXT,agent TEXT,message TEXT,created TEXT);
INSERT INTO "events" VALUES(1,'47369fa25c56481e','created','route','','Chủ nhiệm đã giao việc.','2026-11-20T14:43:57.059+00:00');
INSERT INTO "events" VALUES(2,'47369fa25c56481e','done','sign','quan','Đã bàn giao theo chính sách 2.0.','2026-11-20T14:43:57.063+00:00');
CREATE TABLE settings(case_id TEXT,agent TEXT,model TEXT,PRIMARY KEY(case_id,agent));
INSERT INTO "settings" VALUES('47369fa25c56481e','yen','fixture-legacy');
CREATE TABLE turns(id INTEGER PRIMARY KEY,case_id TEXT,stage TEXT,agent TEXT,model TEXT,answer TEXT,ms INTEGER,tokens INTEGER,created TEXT);
INSERT INTO "turns" VALUES(1,'47369fa25c56481e','deliver','yen','fixture-legacy','{"text":"Đã giao phiên bản 2.0","files":[{"name":"main.py","content":"def add(a, b):\n    return a + b\n"}]}',9,31,'2026-11-20T14:43:57.063+00:00');
PRAGMA user_version=2;
