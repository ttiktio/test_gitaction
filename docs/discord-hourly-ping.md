# Discord Hourly Time Ping

วัดว่า GitHub Actions รัน **ช้ากว่าเวลาที่ตั้ง cron ไว้เท่าไหร่** โดยส่งรายงานเข้า Discord ทุกชั่วโมง

- Workflow: `.github/workflows/discord-hourly-ping.yml`
- Script: `discord_time_ping.py` (ใช้ standard library ล้วน ไม่ต้อง `pip install`)
- Log สะสม: `logs/run_delays.csv` (commit กลับเข้า repo อัตโนมัติทุกครั้งที่รันตามเวลา)

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
DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...' python3 discord_time_ping.py
```

## ข้อควรรู้เรื่อง cron ของ GitHub

- cron ใช้ **UTC เสมอ** — `0 * * * *` คือนาทีที่ 0 ของทุกชั่วโมง ตรงกับนาทีที่ 0 ตามเวลาไทยพอดี
- GitHub ระบุไว้เองว่า scheduled workflow **อาจดีเลย์ได้** โดยเฉพาะช่วงนาทีต้นชั่วโมงที่คนตั้ง cron กันเยอะ ดีเลย์ 5–20 นาทีเป็นเรื่องปกติ และบางครั้งอาจถูกข้ามไปเลย — ซึ่งก็คือสิ่งที่โปรเจคนี้ตั้งใจวัด
- ถ้า repo ไม่มี activity นาน 60 วัน GitHub จะปิด scheduled workflow อัตโนมัติ
