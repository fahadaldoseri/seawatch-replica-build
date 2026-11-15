# نسخة محلية ثابتة من موقع منشور

يبني سكربت واحد نسخة ثابتة من موقع منشور، ويوحّد اسمه ومظهره، ثم ينشره على Cloudflare Pages يوميًا.

## الاستخدام المحلي

```bash
python3 tools/build_replica.py --source https://example.com/ --out out --zip
```

رموز الخروج: 0 نسخة كاملة، 3 منقوصة مع قائمة النقص في `manifest.json`، 1 فاشلة.

## النشر الآلي

يحتاج المستودع إلى:
- سر `CLOUDFLARE_API_TOKEN` بصلاحية Cloudflare Pages Edit.
- متغير `CLOUDFLARE_ACCOUNT_ID`.

ويُعدَّل عنوان المصدر في `.github/workflows/replica-deploy.yml`.
