"use client";
import { useMotionDialog } from "../../lib/motion";
import workspaceStyles from "./workspace.module.css";
import WorkflowCard, { type WorkflowView } from "./workflow-card";
const base: WorkflowView = {
  schema_version: 1,
  type: "workflow",
  id: "gallery",
  session_id: "gallery",
  kind: "contact",
  revision: 0,
  title: "Müşteri / irtibat ekle",
  step: "details",
  steps: ["details", "review", "result"],
  status: "awaiting_input",
  fields: {},
  errors: {},
  primary_action: "continue",
  primary_label: "Devam et",
  result: {},
  controls: [
    { key: "name", label: "Ad soyad", control: "text", required: true, options: {} },
    { key: "email", label: "E-posta", control: "email", required: false, options: {} },
  ],
};
export default function WorkflowGallery() {
  const motionDialog = useMotionDialog();
  const variants: [string, Partial<WorkflowView>][] = [
    [
      "WhatsApp gönderim incelemesi",
      {
        kind: "outreach",
        step: "review",
        status: "ready",
        primary_action: "complete",
        primary_label: "Gönderimi başlat",
        fields: {
          recipients: "+905550102030",
          template: "offer",
          consent_evidence: "Test kaydı · Web formu, 14.09.2026",
        },
        controls: [
          {
            key: "recipients",
            label: "Alıcı telefonları",
            control: "textarea",
            required: true,
            options: {},
          },
          {
            key: "template",
            label: "Meta onaylı şablon",
            control: "select",
            required: true,
            options: { offer: "teklif_formu · tr" },
          },
          {
            key: "consent_evidence",
            label: "Tanıtım izninin kaynağı ve tarihi",
            control: "textarea",
            required: false,
            options: {},
          },
        ],
        output: {
          template_preview: {
            name: "teklif_formu",
            language: "tr",
            body: "Teklif ön hazırlığı için ürün ve teslimat bilgilerinizi form üzerinden paylaşabilirsiniz. Nihai ürün uygunluğu ve ticari koşullar ekibimiz tarafından doğrulanır.",
            footer: "Örnek işletme",
            buttons: ["Formu aç"],
            status: "Meta onaylı · şablon metni değiştirilemez",
          },
        },
      },
    ],
    [
      "Boş kayıt listesi",
      {
        kind: "records",
        title: "Teknik talepler",
        controls: [],
        records: [],
        primary_action: null,
        primary_label: null,
      },
    ],
    [
      "Talep ayrıntısı",
      {
        kind: "records",
        title: "Talep ayrıntısı",
        controls: [],
        records: [
          {
            id: "request",
            title: "Deniz Yılmaz",
            subtitle: "İnceleniyor",
            details: { "Eksik bilgiler": "Çap", "Son not": "Ölçü bilgisi bekleniyor." },
            actions: [{ operation: "note", label: "Not ekle" }],
          },
        ],
      },
    ],
    [
      "Taslak inceleme",
      {
        kind: "configure",
        title: "Asistanı düzenle",
        controls: [],
        step: "review",
        status: "ready",
        changes: [{ label: "Hizmetler", before: "Üretim", after: "Üretim ve bakım" }],
        primary_action: "complete",
        primary_label: "Taslağa kaydet",
      },
    ],
    [
      "Geri alma incelemesi",
      {
        kind: "rollback",
        title: "Sürümü geri al",
        step: "review",
        status: "ready",
        controls: [],
        output: { summary: "2. sürümden yeni bir canlı sürüm oluşturulacak." },
        primary_action: "complete",
        primary_label: "Seçilen sürümü canlıya al",
      },
    ],
    [
      "Model testi hatası",
      {
        kind: "test",
        title: "Asistanı test et",
        status: "failed",
        controls: [
          {
            key: "content",
            label: "Müşteri mesajı",
            control: "textarea",
            required: true,
            options: {},
          },
        ],
        fields: { content: "Ürünleriniz hakkında bilgi alabilir miyim?" },
        errors: { content: "Model yanıt vermedi. Mesajınız korundu." },
      },
    ],
    [
      "Gönderim kuyruğu",
      {
        kind: "outreach",
        title: "Toplu gönderim",
        status: "running",
        controls: [],
        primary_action: null,
        primary_label: null,
        output: {
          summary: "3 alıcı kuyruğa alındı.",
          delivery_note: "Kuyruğa alınması teslim edildiği anlamına gelmez.",
        },
      },
    ],
    [
      "Belirsiz teslim durumu",
      {
        kind: "reply",
        title: "Müşteriye yanıt",
        status: "completed",
        step: "result",
        controls: [],
        primary_action: null,
        primary_label: null,
        result: { message: "Gönderim sonucu belirsiz. Otomatik tekrar yapılmayacak." },
      },
    ],
    [
      "Şirket sahibi daveti",
      {
        kind: "owner_invite",
        title: "Şirket sahibini davet et",
        status: "completed",
        step: "result",
        controls: [],
        primary_action: null,
        primary_label: null,
        result: { message: "Davet bağlantısı hazır. Üyelik kabulden sonra başlar." },
      },
    ],
    [
      "Mevcut kişi eşleşmesi",
      {
        status: "completed",
        step: "result",
        controls: [],
        primary_action: null,
        primary_label: null,
        result: { message: "Bu e-posta ile mevcut kişi bulundu." },
        records: [
          {
            id: "duplicate",
            title: "Deniz Yılmaz",
            subtitle: "Örnek şirket",
            details: { "E-posta": "deniz@example.com" },
            actions: [],
          },
        ],
      },
    ],
    ["İptal", { status: "cancelled", primary_action: null, primary_label: null }],
    ["Boş", {}],
    ["Dolu", { fields: { name: "Deniz Yılmaz", email: "deniz@example.com" } }],
    ["Yükleniyor", { status: "running", primary_action: null, primary_label: null }],
    [
      "Alan hatası",
      {
        fields: { email: "hatalı" },
        errors: { name: "Ad soyad gerekli.", email: "Geçerli bir e-posta yazın." },
      },
    ],
    ["Yetkisiz — eylemler kapalı", {}],
    [
      "Bekletildi",
      { status: "paused", primary_action: "resume", primary_label: "Kaldığım yerden devam et" },
    ],
    [
      "Tamamlandı",
      {
        status: "completed",
        step: "result",
        fields: { name: "Deniz Yılmaz" },
        result: { message: "Deniz Yılmaz kişi olarak kaydedildi." },
        primary_action: null,
        primary_label: null,
      },
    ],
  ];
  return (
    <main style={{ maxWidth: 720, margin: "40px auto", padding: 20 }}>
      <h1>Ortak işlem bileşenleri</h1>
      <p>Geliştirme ortamı durum galerisi. Örnek eylemler kapalıdır.</p>
      <button onClick={motionDialog.open}>Motion: geçmiş panelini aç</button>
      <dialog ref={motionDialog.ref} className={workspaceStyles.historyDrawer} aria-label="Motion testi" onCancel={e => { e.preventDefault(); motionDialog.close(); }}>
        <h2>Geçmiş paneli</h2>
        <p>Açılış, kapanış ve Escape aynı hareket sözleşmesini kullanır.</p>
        <button onClick={motionDialog.close}>Paneli kapat</button>
      </dialog>
      {variants.map(([label, variant], i) => (
        <section key={label}>
          <h2>{label}</h2>
          <WorkflowCard
            view={{ ...base, ...variant, id: `gallery-${i}` }}
            disabled
            refresh={async () => {}}
          />
        </section>
      ))}
    </main>
  );
}
