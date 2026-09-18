# Discord Hourly Time Ping

วัดว่า GitHub Actions รัน **ช้ากว่าเวลาที่ตั้ง cron ไว้เท่าไหร่** โดยส่งรายงานเข้า Discord ทุกชั่วโมง

- Workflow: `.github/workflows/discord-hourly-ping.yml`
- Script: `discord_time_ping.py` (ใช้ standard library ล้วน ไม่ต้อง `pip install`)
- Log สะสม: `logs/run_delays.csv` (commit กลับเข้า repo อัตโนมัติทุกครั้งที่รายงาน)

## ทำไม cron ยิง 4 ครั้งต่อชั่วโมง

cron ตั้งไว้ `0,15,30,45 * * * *` แต่ **ส่ง Discord แค่ชั่วโมงละครั้ง**

เพราะ GitHub **ทิ้ง** scheduled trigger ทิ้งไปเฉยๆ เวลาระบบงานล้น โดยเฉพาะนาทีที่ 0
ที่คนตั้ง cron ชนกันทั้งโลก และ **ไม่มีการรันย้อนหลังให้** รอบที่โดนทิ้งคือหายไปเลย
ตอนตั้ง `0 * * * *` อย่างเดียว 2 วันแรกได้จริงแค่ 9 จาก 46 รอบ

รอบที่ `:15 :30 :45` จึงเป็น **รอบสำรอง** — script จะเช็ค `logs/run_delays.csv` ก่อน
ถ้าชั่วโมงนั้นรายงานไปแล้วก็จบเงียบๆ ไม่ส่งซ้ำ (flag `--once-per-slot`)

ผลคือ ถ้ารอบ `:00` โดนทิ้ง จะเสียความแม่นยำแค่ไม่กี่นาที แทนที่จะเสียทั้งชั่วโมง
ส่วนค่าดีเลย์ยังวัดเทียบ **นาทีที่ 0** เสมอ (ตั้งได้ที่ env `TARGET_MINUTE`)
และในข้อความจะบอกด้วยว่ารอบไหนเป็นตัวที่ยิงติด

## เวลา 3 จุดที่เทียบกัน

| จุด | ความหมาย |
|---|---|
| `scheduled` | ช่อง cron ที่ควรจะรัน เช่น `14:00:00 UTC` |
| `queued` | เวลาที่ GitHub สร้าง workflow run จริง (ดึงจาก Actions API) |
| `executed` | เวลาที่ script ยิงข้อความ = queued + เวลาบูต runner + setup steps |

ค่าดีเลย์ที่รายงาน:

- **คิวของ GitHub** = `queued - scheduled` → ความช้าจากตัว scheduler ของ GitHub เอง
- **รวมทั้งหมด** = `executed - scheduled` → ความช้าจริงก่อนงานได้เริ่มทำ

สีของ embed: เขียว < 2 นาที, เหลือง < 10 นาที, แดงเมื่อเกินนั้น

## ติดตั้ง

1. ใส่ webhook เป็น repository secret — **ห้ามฝังในโค้ด** เพราะ repo นี้เป็น public และ Discord จะลบ webhook ที่โผล่ในโค้ดสาธารณะทิ้งอัตโนมัติ

   `Settings` → `Secrets and variables` → `Actions` → `New repository secret`

   - Name: `DISCORD_WEBHOOK_URL`
   - Secret: URL webhook ของ Discord

2. merge workflow เข้า branch หลัก (`main`) — **cron จะทำงานเฉพาะบน default branch เท่านั้น** อยู่บน branch อื่นจะไม่ยิงตามเวลา
3. ทดสอบได้ทันทีที่แท็บ `Actions` → `Discord Hourly Time Ping` → `Run workflow`

## รันในเครื่อง

```bash
python3 discord_time_ping.py --dry-run              # พิมพ์ payload ไม่ส่งจริง
python3 discord_time_ping.py --dry-run --target-minute 30   # เปลี่ยนนาทีเป้าหมาย
DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...' python3 discord_time_ping.py
```

## ข้อควรรู้เรื่อง cron ของ GitHub

- cron ใช้ **UTC เสมอ** — นาทีที่ 0 ของ UTC ตรงกับนาทีที่ 0 ตามเวลาไทยพอดี (UTC+7 ต่างกันแบบเต็มชั่วโมง)
- GitHub ระบุไว้เองว่า scheduled workflow **อาจดีเลย์หรือถูกข้ามได้** โดยเฉพาะช่วงต้นชั่วโมง — ดีเลย์ 5–20 นาทีเป็นเรื่องปกติ
- cron ที่ถี่ที่สุดที่ GitHub ยอมคือทุก 5 นาที ถ้าอยากได้ความแม่นยำมากกว่านี้ ลดช่วงรอบสำรองลงได้ (เช่น `*/5 * * * *`) แลกกับจำนวน run ที่มากขึ้น
- ถ้า repo ไม่มี activity นาน 60 วัน GitHub จะปิด scheduled workflow อัตโนมัติ — แต่ repo นี้ commit log ทุกชั่วโมงอยู่แล้วจึงไม่โดน
