# Uyumluluk (Compliance) — LeadPulse

**Prensip: Yasal olmayan hiçbir mesaj gönderilmez. Her outbound mesaj compliance gate'inden geçmek zorundadır.**

## Kurallar (sırayla — biri BLOCK derse mesaj gitmez)

1. **Opt-out listesi** — `opt_outs` tablosunda `(tenant_id, phone_e164)` bulunursa → BLOCK.
2. **`consent_status = OPT_OUT`** → BLOCK.
3. **30 gün cooldown** — Aynı numaraya son 30 gün içinde sent/delivered/read olmuş bir mesaj varsa → BLOCK (next_allowed_at = last_sent + 30d).
4. **Quiet hours** — Hedef ülkenin yerel saati 09:00–18:00 dışındaysa → **DEFER** (block değil; bir sonraki 09:00 lokale ötelenir).
5. **İYS (yalnızca TR numaraları)** — Marka bazlı ret varsa → BLOCK. Şu an placeholder; production'da gerçek İYS Marka partner entegrasyonu şart.
6. **Auto opt-out algılama** — Inbound webhook'ta `STOP / DUR / İSTEMİYORUM / UNSUBSCRIBE / STOPP / الإلغاء / СТОП` regex'ini yakalar, `OptOutSource.USER_REPLY` ile otomatik ekler.

## Meta / WhatsApp Business platform politikaları

- Yalnızca **Meta tarafından onaylı Message Template**'ler kullanılabilir (24h session penceresi dışında).
- Session içindeki serbest metin yanıtları (agent'ın inbox'tan yazdığı) 24 saatlik pencere içinde olmalı.
- Sender profilleri **warmup** tier'larına tabidir (T1 1K/gün → T4 sınırsız). `daily_cap` ihlali → sender skipping.
- WABA kalite skoru "flagged" / "banned" olursa `SenderHealthStatus` güncellenir; dispatcher o gönderene iş vermez.

## KVKK / GDPR

- **Aydınlatma metni** ve **açık rıza** kayıtları `audit_logs` üzerinde tutulur.
- Veri sahibi silme talebi: `DELETE /api/v1/leads/{id}` + ilgili conversations/messages cascade siler. Opt-out kaydı (hash veya salt'lı temsili) tutulur ki tekrar mesaj gönderilmesin.

## Audit

- `AuditLog` tablosu tüm hassas eylemleri (`add_opt_out`, kullanıcı rol değişiklikleri, template onayı, ...) actor_id + IP + user-agent ile kayıt altına alır.

## KRİTİK

- **Compliance ASLA "unknown = allow" yapmaz.** İYS erişimi yoksa uyarı log'lanır ama TR gönderimi güvenli sayılmaz — production'da İYS partneri zorunludur.
- Cooldown süresini sektöre göre uzatmak isterseniz `_COOLDOWN_DAYS` sabitini sektöre bağlı hale getirin.
