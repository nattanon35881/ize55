# ize55
# บอทหุ้น Telegram

## 📋 สรุปคำสั่งทั้งหมด

**เช็คราคา (พิมพ์ตรงๆ ไม่ต้องมี /)**
- ชื่อหุ้น เช่น `PTT`, `AAPL` → ราคา, P/E, ROE, DCF, Bias รายเดือน, ข่าว
- ทอง/ดัชนี/คู่เงิน เช่น `XAUUSD`, `US100`, `EURUSD` → ราคาสด + Bias Fibo 4H + เช็คลิสต์

**Trade Journal**
- `/log` — ถามทีละขั้นตอนพร้อมปุ่ม (บันทึกวินัย IDM/Turtle Soup ด้วย)
- `/log BUY XAUUSD 2650 2620 2700 [หมายเหตุ]` — พิมพ์รวดเดียว
- `/close` — เลือกไม้จากปุ่ม (มีปุ่มลัด TP/SL)
- `/close ID ราคา` — ปิดไม้ตรงๆ
- `/trades` — ไม้ที่เปิดอยู่
- `/stats` — สรุปผล + win rate แยกตามวินัย
- `/cancel` — ยกเลิกฟอร์มค้าง

**Watchlist**
- `/watch SYMBOL [SYMBOL ...]` / `/unwatch SYMBOL [...]` / `/watchlist`

**Price Alert**
- `/alert SYMBOL ราคา` / `/alerts` / `/unalert ID`

**Macro**
- `/macro` — DXY / US10Y Yield / SET Index

**Risk**
- `/size SYMBOL ทุน RISK% ราคาเข้า SL` — คำนวณขนาดโพซิชัน
- `/setlimit daily หรือ weekly ค่าR` — ตั้งวงเงินขาดทุน auto lockout
- `/limits` — เช็คสถานะ

**อื่นๆ**
- `/start` หรือ `/help` — เมนูช่วยเหลือ + Chat ID

**หน้าเว็บ (เปิดผ่านลิงก์)**
- แดชบอร์ดเทรด: `/dashboard/DASHBOARD_SECRET`
- แดชบอร์ด Macro: `/macro-dashboard/DASHBOARD_SECRET`

---

พิมพ์ชื่อย่อหุ้นในแชท แล้วบอทจะตอบราคา, % เปลี่ยนแปลง, P/E, P/B, EPS, ROE, ROA, Net/Gross Margin, Market Cap และข่าวล่าสุด 3 ข่าว

รองรับทั้งหุ้นไทย (SET) และหุ้นต่างประเทศ — พิมพ์แค่ชื่อย่อเฉยๆ เช่น `PTT`, `ADVANC`, `KBANK` หรือ `AAPL`, `GOOGL`, `NVDA` บอทจะลองหาแบบชื่อตรงๆ ก่อน ถ้าไม่เจอจะลองเติม `.BK` ให้อัตโนมัติ (แบบที่ Yahoo Finance ใช้เรียกหุ้นไทย)

ข้อมูลราคา/ข่าว/พื้นฐานดึงจาก Yahoo Finance ผ่านไลบรารี `yfinance` (ฟรี ไม่ต้องขอ API key)

## ขั้นตอนติดตั้ง

### 1. สร้างบอทใน Telegram
1. เปิดแชทกับ `@BotFather` ใน Telegram
2. พิมพ์ `/newbot` แล้วตั้งชื่อบอทตามที่ต้องการ
3. BotFather จะให้ **token** มา (หน้าตาประมาณ `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`) เก็บไว้ ใช้ในขั้นตอนที่ 3

### 2. อัปโหลดโค้ดขึ้น GitHub
สร้าง repo ใหม่ (public หรือ private ก็ได้) แล้ว push โฟลเดอร์นี้ (`app.py`, `requirements.txt`) ขึ้นไป

### 3. Deploy ฟรีบน Render.com
1. สมัครบัญชีที่ [render.com](https://render.com) (ฟรี ไม่ต้องผูกบัตรเครดิตสำหรับ Web Service ฟรี)
2. กด **New +** → **Web Service** → เชื่อมกับ GitHub repo ที่เพิ่ง push
3. ตั้งค่า:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
4. ในแท็บ **Environment** เพิ่มตัวแปร:
   - `TELEGRAM_TOKEN` = token จาก BotFather
5. กด Deploy รอสักครู่ จะได้ URL ประมาณ `https://ชื่อแอพ.onrender.com`

### 4. ผูก Webhook ให้ Telegram รู้จัก URL ของเรา
เปิดลิงก์นี้ในเบราว์เซอร์ครั้งเดียว (แทนที่ `<TOKEN>` และ `<URL>` ด้วยของจริง):

```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=<URL>/webhook/<TOKEN>
```

ตัวอย่าง:
```
https://api.telegram.org/bot123456789:AAExxxx/setWebhook?url=https://my-stock-bot.onrender.com/webhook/123456789:AAExxxx
```

เห็นข้อความ `"ok":true` แปลว่าเชื่อมสำเร็จ

### 5. ทดสอบ
เปิดแชทกับบอทของคุณใน Telegram แล้วพิมพ์ `PTT` หรือ `AAPL` ดู

## ข้อควรรู้
- **Render free tier จะ "หลับ" หลังไม่มีคนใช้งาน 15 นาที** พอมีข้อความเข้ามาครั้งแรกหลังจากหลับ บอทจะตอบช้าประมาณ 30-60 วินาที (รอบแรกเท่านั้น) ถ้าอยากให้ตื่นตลอด สามารถใช้บริการ ping ฟรีอย่าง cron-job.org ให้เรียก URL หลัก (`https://ชื่อแอพ.onrender.com/`) ทุก 10 นาที
- yfinance ดึงข้อมูลจาก Yahoo Finance ฟรี แต่บางครั้ง Yahoo อาจจำกัดจำนวนคำขอ (rate limit) ถ้าเจอปัญหาข้อมูลไม่ขึ้น ให้ลองพิมพ์ใหม่อีกครั้งในอีกสักครู่

## แจ้งเตือน Forex (ทองคำ XAUUSD) ตามระบบ ize

บอทจะเช็ค Bias จาก Fibonacci บน 4H ให้อัตโนมัติ พร้อมส่งเช็คลิสต์ที่เหลือ (1H zone, IDM, TP/RR/SL, Turtle Soup) ให้ดูด้วยตาเองตามระบบ — ส่งแจ้งเตือนตามเวลาที่ตั้งไว้ (ค่าเริ่มต้นในตัวอย่าง: 8:00, 12:30, 14:00, 18:30, 20:00)

**เพราะ Render free tier รันสคริปต์ค้างไว้ตลอดเวลาไม่ได้ (จะหลับ) เราเลยใช้บริการ cron ฟรีจากภายนอกมา "กด" ให้บอทส่งข้อความตามเวลาแทน** ขั้นตอนตั้งค่า:

### 1. หา Chat ID ของตัวเอง
เปิดแชทกับบอทใน Telegram พิมพ์ `/start` — บอทจะตอบกลับ Chat ID มาให้ในข้อความ (ตัวเลขยาวๆ) คัดลอกเก็บไว้

### 2. เพิ่ม Environment Variables ใน Render
ไปที่ Settings → Environment เพิ่ม 2 ตัวแปรนี้:
- `FOREX_CHAT_ID` = Chat ID จากขั้นตอนที่ 1
- `FOREX_CRON_SECRET` = ตั้งรหัสลับเองอะไรก็ได้ (สุ่มยาวๆ ป้องกันคนอื่นมายิง URL แจ้งเตือนแทนเรา) เช่น `ize2026secretkey`

กด Save — Render จะ deploy ใหม่ให้อัตโนมัติ

### 3. ตั้งเวลาแจ้งเตือนที่ cron-job.org (ฟรี)
1. สมัครบัญชีฟรีที่ [cron-job.org](https://cron-job.org)
2. สร้าง cronjob ใหม่ 5 อัน (1 อันต่อ 1 เวลาแจ้งเตือน) แต่ละอันตั้ง URL เป็น:
   ```
   https://ชื่อแอพ.onrender.com/forex-check/ize2026secretkey
   ```
   (แทนที่ `ize2026secretkey` ด้วยค่า `FOREX_CRON_SECRET` ที่ตั้งไว้จริง)
3. ตั้งเวลาแต่ละอันตาม 5 รอบที่ต้องการ — ถ้าระบบให้เลือก timezone ได้ ให้เลือก `Asia/Bangkok` แล้วใส่เวลาตรงๆ (8:00, 12:30, 14:00, 18:30, 20:00) ถ้าระบบรับเฉพาะ UTC ให้ลบ 7 ชั่วโมง: 01:00, 05:30, 07:00, 11:30, 13:00 UTC
4. บันทึกทั้ง 5 cronjob

ครบแล้วบอทจะส่งข้อความ Bias + เช็คลิสต์เข้าแชทให้อัตโนมัติตามเวลาที่ตั้งไว้ทุกวัน

**หมายเหตุ:** เวอร์ชันนี้คำนวณเฉพาะ Bias จาก Fibonacci 4H ให้อัตโนมัติเท่านั้น ส่วนแนวรับ-แนวต้าน 1H (RBS, SBR, OCL, QM, OB), IDM, และสัญญาณ Turtle Soup บน M1/M5 ยังต้องดูด้วยตาเองตามระบบ เพราะ pattern พวกนี้เขียนโค้ดตรวจจับอัตโนมัติให้แม่นยำ 100% ได้ยากมาก

## Trade Journal (บันทึกไม้ที่เทรดจริง)

เก็บข้อมูลลง Google Sheets ของคุณเอง ผ่าน Google Apps Script (ไม่ต้องสมัคร Google Cloud, ไม่ต้องใช้ service account — ตั้งค่าในตัวชีทเลย)

### 1. สร้าง Google Sheet ใหม่
เปิด [sheets.google.com](https://sheets.google.com) สร้างชีทเปล่าใหม่ 1 อัน ตั้งชื่ออะไรก็ได้ เช่น "Trade Journal"

### 2. วางสคริปต์ลงในชีท
1. ในชีทที่สร้าง ไปที่เมนู **Extensions → Apps Script**
2. จะเปิดหน้าต่างแก้โค้ดขึ้นมา ลบโค้ด default (`function myFunction() {}`) ออกให้หมด
3. เปิดไฟล์ `trade-journal-appsscript.gs` ที่ผมส่งให้ คัดลอกเนื้อหาทั้งหมด มาวางแทน
4. กด **Save** (ไอคอนรูปแผ่นดิสก์ หรือ Ctrl/Cmd+S)

### 3. Deploy เป็น Web App
1. มุมขวาบนกด **Deploy → New deployment**
2. ตรงช่อง "Select type" กดไอคอนรูปเฟือง เลือก **Web app**
3. ตั้งค่า: **Execute as: Me**, **Who has access: Anyone**
4. กด **Deploy**
5. จะมี popup ขอ authorize สิทธิ์ — กด **Authorize access** → เลือกบัญชี Google ของคุณ → ถ้าขึ้นเตือน "Google hasn't verified this app" ให้กด **Advanced** → **Go to (ชื่อโปรเจกต์) (unsafe)** → **Allow** (ปลอดภัยครับ เพราะเป็นสคริปต์ของคุณเอง ไม่ใช่ของคนอื่น)
6. จะได้ **Web app URL** ยาวๆ ขึ้นต้นด้วย `https://script.google.com/macros/...` **คัดลอกเก็บไว้**

### 4. ใส่ URL ใน Render
ไปที่ Render → Environment เพิ่มตัวแปรใหม่:
- `SHEET_WEBAPP_URL` = URL จากขั้นตอนที่ 3

กด Save Changes รอ deploy ใหม่เสร็จ

### 5. ใช้งานผ่าน Telegram
- `/log BUY XAUUSD 2650 2620 2700` — บันทึกไม้ใหม่ (ฝั่ง entry SL TP) บอทจะตอบเลขไม้กลับมา เช่น #1
- `/close 1 2680` — ปิดไม้ #1 ที่ราคา 2680 บอทคำนวณผลเป็น R-multiple ให้อัตโนมัติ (เทียบระยะ SL)
- `/trades` — ดูไม้ที่ยังเปิดอยู่ทั้งหมด
- `/stats` — สรุป win rate และผลรวมเป็น R

ข้อมูลทุกไม้จะโชว์เป็นแถวในชีท "Trades" ที่สร้างขึ้นอัตโนมัติ เปิดดู/แก้ไขตรงๆ ในชีทได้เลยตลอดเวลา

## Watchlist หุ้น + แจ้งเตือนราคา

ใช้ Google Sheet และ Apps Script Web App ตัวเดียวกับ Trade Journal (แค่โค้ดใน Apps Script อัปเดตเพิ่ม ไม่ต้องสร้าง Sheet ใหม่) จะสร้างแท็บ "Watchlist" และ "Alerts" ให้อัตโนมัติ

### คำสั่งที่ใช้ได้
- `/watch PTT` — เพิ่ม PTT เข้า watchlist
- `/unwatch PTT` — เอาออก
- `/watchlist` — ดูราคาสดของทุกตัวใน watchlist ตอนนี้
- `/alert PTT 35` — ตั้งแจ้งเตือนเมื่อราคาถึง 35 (บอทดูเองว่าราคาตอนนี้สูง/ต่ำกว่าเป้า แล้วรู้เองว่าต้องรอราคาขึ้นหรือลง)
- `/alerts` — ดูแจ้งเตือนที่ตั้งไว้ทั้งหมด
- `/unalert 1` — ยกเลิกแจ้งเตือน #1

### ตั้งเวลาแจ้งเตือนอัตโนมัติ (เพิ่มเติมจาก cron 5 รอบของ Forex)
เพิ่ม cronjob ใหม่ที่ [cron-job.org](https://cron-job.org) อีก 2 อัน:

1. **สรุป Watchlist ทุกเช้า** — ตั้งเวลาที่ต้องการ (เช่น 9:00 เวลาไทย) ยิงไปที่:
   ```
   https://ชื่อแอพ.onrender.com/watchlist-check/FOREX_CRON_SECRET
   ```
2. **เช็คแจ้งเตือนราคา** — ตั้งให้รันทุก 15-30 นาที (ในช่วงเวลาตลาดเปิด) ยิงไปที่:
   ```
   https://ชื่อแอพ.onrender.com/alert-check/FOREX_CRON_SECRET
   ```

(ใช้ `FOREX_CRON_SECRET` ตัวเดิมที่ตั้งไว้แล้วตอนทำแจ้งเตือน Forex ไม่ต้องสร้างใหม่)
