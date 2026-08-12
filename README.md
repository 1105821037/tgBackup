# tgBackup

tgBackup 是一个把 Telegram 聊天保存到自己电脑或服务器上的备份工具。


简单来说，它做两件事：

- 尽可能完整地把你的 Telegram 聊天和媒体保存下来；
- 即使你以后登录不上Telegram，或者是某个聊天被删除、封锁，也能像翻聊天客户端一样查看已经备份的内容。


## 能备份什么

几乎所有telegram消息类型，目前支持保存：

- 私聊、群组、超级群组、频道和“收藏夹”；
- 消息正文、发送者、发送时间、回复关系、转发来源和编辑记录；
- 图片、视频、文件、语音、音频、动图和贴纸；
- 相册、联系人、位置、投票、骰子、服务消息等特殊内容；
- 消息的浏览量、转发数、回复数和回应等状态信息；
- 联系人、群组和频道的名称、头像及资料变化。

每个会话都可以单独设置备份规则。第一次运行会从头保存历史消息，之后只同步新增内容，不需要每次重新下载全部聊天。

如果消息后来被编辑或删除，“历史消息更新”功能可以定期检查已经归档的内容，并把这些变化记录下来。旧版本不会直接被覆盖，之后仍然可以查看。

媒体类型可以按会话选择。如果希望做尽可能完整的备份，请在规则里勾选全部媒体类型。




## 开始

你需要准备：

- Python 和 Node.js；
- MySQL；
- 一个 Telegram API ID 和 API Hash，可在 [my.telegram.org](https://my.telegram.org) 的 **API development tools** 中申请；
- 一台有足够磁盘空间的电脑或服务器。

媒体文件会原样保存在磁盘上。群组和频道较多时，空间占用可能很快增长，建议提前规划好数据盘和备份策略。

## 运行

下面以 Windows PowerShell 为例。

### 1. 下载项目并安装后端依赖

```powershell
git clone https://github.com/1105821037/tgBackup.git
cd tgBackup

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Linux 上可将 Python 命令换成：

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

### 2. 填写配置

复制一份环境变量示例：

```powershell
Copy-Item .env.example .env
```

然后打开 `.env`，至少填写这些内容：

```env
TELEGRAM_API_ID=你的_API_ID
TELEGRAM_API_HASH=你的_API_HASH

MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=tg_backup
MYSQL_PASSWORD=你的_MySQL_密码
MYSQL_DATABASE=tg_backup
```


### 3. 初始化数据库

确保 MySQL 已经启动，然后运行：

```powershell
.\.venv\Scripts\python.exe backend\scripts\init_database.py
```

初始化脚本会按配置创建数据库和表。

### 4. 构建网页

```powershell
cd frontend
npm ci
npm run build
cd ..
```

Vite 只在构建网页时使用。构建完成后，网页会放在 `frontend/dist`，日常运行不需要再启动 Vite。

### 5. 启动

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。

第一次打开时，页面会让你创建管理员账号。进入系统后，再按提示输入手机号、验证码和 Telegram 两步验证密码完成登录。

绑定完成后，到“会话”页面选择想保存的聊天，设置执行时间和媒体类型即可开始备份。

## 在服务器上使用

如果只在本机访问，上面的启动方式就够了。需要从外网访问时，建议在前面放置 Nginx、Caddy 或其他反向代理，并启用 HTTPS。

生产环境的 `.env` 至少需要调整：

```env
COOKIE_SECURE=true
FRONTEND_ORIGIN=https://你的域名
```

Telegram Session 同一时间只能由一个进程安全持有，所以 Uvicorn 必须保持 **单 worker**。不要使用 `--workers 2` 或更大的值。程序本身也会尝试阻止多个后端实例同时占用相同的 Session。

建议让 Uvicorn 只监听本机地址，再由反向代理对外提供服务：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

## 数据保存在哪里

默认情况下：

- Telegram 登录 Session：`data/accounts/`
- 聊天媒体：`data/media/`
- 联系人、群组和频道头像：`data/avatars/`
- 资料加密密钥：`data/profile.key`
- 消息、规则和运行记录：MySQL

聊天媒体大致按下面的结构保存：

```text
data/media/user_<用户ID>/<会话ID>/<消息ID>/
```

只复制媒体目录并不算完整备份。迁移或做灾备时，请一起保存：

- MySQL 数据库；
- 整个 `data` 目录；
- 当前使用的 `.env`。

其中 `data/profile.key` 用于解密资料中的敏感字段，丢失后无法重新解密这些内容。请把它和数据库备份放在安全的位置。

## 更新项目

更新代码后，通常需要重新安装依赖并构建网页：

```powershell
git pull
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

cd frontend
npm ci
npm run build
cd ..
```

然后重启后端服务。

目前项目还没有正式的数据库迁移系统。升级前建议先备份 MySQL 和 `data` 目录，并留意版本说明中是否提到数据结构变化。

## 常见问题

### Telegram 无法访问后，已经备份的消息还能看吗？

可以。归档页面读取的是本地数据。断线只会影响新消息同步和需要临时访问 Telegram 的功能。

### 备份后，某个历史消息被修改、删除会怎样？

开启“历史消息更新”后，系统会定期重新检查已经归档的消息。发现编辑时会增加新版本，发现删除时会记录删除状态。

### 如何删除已同步内容？

我们暂不支持单独删除某个聊天的消息，如果你需要清除所有数据重新开始，
下面的命令会重建数据库，已有数据库内容会被删除，只适合确认不再需要旧数据时使用：

```powershell
.\.venv\Scripts\python.exe backend\scripts\init_database.py --reset
```

媒体和 Session 文件是否保留，需要再根据自己的需求手动处理。执行前务必做好备份。

## 隐私与安全

tgBackup 保存的是非常私密的数据。建议至少做到：

- 不要提交或分享 `.env`、`data/accounts` 和 `data/profile.key`；
- 外网访问必须使用 HTTPS；
- 使用足够长且不重复的后台密码；
- 定期备份数据库和 `data` 目录；
- 限制服务器登录权限，并及时安装系统安全更新；
- 不要把备份目录直接暴露成公开静态文件目录。

所有归档媒体都通过登录后的接口读取，不需要也不应该由 Web 服务器直接公开 `data` 目录。

## 开发

如果你准备修改前端，可以分别启动后端和 Vite 开发服务器。

后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
```

前端：

```powershell
cd frontend
npm install
npm run dev
```

然后打开 [http://localhost:5173](http://localhost:5173)。开发服务器会把 `/api` 请求转发给本地后端。

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

构建检查：

```powershell
cd frontend
npm run build
```
