"use client";

import {
  ArrowLeft,
  BadgeCheck,
  Bot,
  Building2,
  Check,
  CheckCheck,
  ChevronRight,
  CircleAlert,
  ClipboardCheck,
  FileSpreadsheet,
  HandHeart,
  HelpCircle,
  ListChecks,
  MessageCircle,
  MoreVertical,
  PackagePlus,
  Paperclip,
  Plus,
  RefreshCcw,
  SendHorizontal,
  ShieldCheck,
  Sparkles,
  Upload,
  UserRound,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useRef, useState, useTransition } from "react";

import type { CustomerTurnAction, CustomerTurnResponse } from "@/lib/simulator/customer-config";
import styles from "./page.module.css";

type Mode = "customer" | "admin";
type AdminStage = "company" | "products" | "rule" | "review";
type CustomerTaskId = "price" | "delivery" | "unknown";
type MessageRole = "assistant" | "customer" | "admin";
type OutcomeKind = CustomerTurnAction;

interface ChatMessage {
  id: number;
  role: MessageRole;
  text: string;
  meta?: string;
  outcome?: OutcomeKind;
}

interface CustomerTask {
  id: CustomerTaskId;
  title: string;
  subtitle: string;
  question: string;
}

interface CustomerOutcome {
  action: CustomerTurnAction | "response-withheld";
  factIds: string[];
  latencyMs?: number;
}

interface DraftOffering {
  id: string;
  name: string;
  price: string;
  source: "Sohbet" | "Excel/PDF";
}

const customerTasks: CustomerTask[] = [
  {
    id: "price",
    title: "Fiyat sor",
    subtitle: "Onaylı bir fiyat bilgisi",
    question: "AX-500'ün fiyatı nedir?",
  },
  {
    id: "delivery",
    title: "Teslimat sor",
    subtitle: "Onaylı teslimat bilgisi",
    question: "AX-500 ne zaman teslim edilir?",
  },
  {
    id: "unknown",
    title: "Bilinmeyen bilgi",
    subtitle: "Kanıtsız bilgi politikası",
    question: "AX-500 için özel renk seçeneği var mı?",
  },
];

const adminSteps: Array<{ id: AdminStage; title: string; detail: string }> = [
  { id: "company", title: "Şirketini tanıt", detail: "Adı ve kısa tanımıyla başla" },
  { id: "products", title: "Ürün / hizmet ekle", detail: "Bir liste oluştur, kapatmayı seç" },
  { id: "rule", title: "Bilgi kuralını seç", detail: "Bilinmeyen bilgiyi güvenle yönet" },
  { id: "review", title: "Önizleme & onay", detail: "Müşteri gibi test et" },
];

const seedCustomerMessages: ChatMessage[] = [
  {
    id: 1,
    role: "assistant",
    text: "Merhaba, Atlas Metal asistanıyım. Size ne konuda yardımcı olabilirim?",
  },
];

const seedAdminMessages: ChatMessage[] = [
  {
    id: 2,
    role: "assistant",
    text: "Merhaba. Şirketini adım adım tanıyalım; önce şirketinin adını yaz.",
  },
];

function classNames(...values: Array<string | false | undefined>) {
  return values.filter(Boolean).join(" ");
}

function getCustomerTask(id: CustomerTaskId) {
  return customerTasks.find((task) => task.id === id) ?? customerTasks[0];
}

function isCustomerTurnResponse(value: unknown): value is CustomerTurnResponse {
  if (typeof value !== "object" || value === null) return false;
  const response = value as Record<string, unknown>;
  return (
    (response.action === "answer" ||
      response.action === "clarify" ||
      response.action === "handoff" ||
      response.action === "converse") &&
    Array.isArray(response.factIds) &&
    response.factIds.every((factId) => typeof factId === "string") &&
    typeof response.reply === "string" &&
    typeof response.latencyMs === "number" &&
    response.engine === "local-llm"
  );
}

function customerTaskWasSatisfied(task: CustomerTask, response: CustomerTurnResponse) {
  if (task.id === "price") return response.factIds.includes("claim/ax-500/list-price");
  if (task.id === "delivery") {
    return response.factIds.includes("claim/ax-500/standard-lead-time");
  }
  return response.action === "handoff";
}

export default function WhatsAppSimulatorPage() {
  const [mode, setMode] = useState<Mode>("customer");
  const [customerMessages, setCustomerMessages] = useState<ChatMessage[]>(seedCustomerMessages);
  const [customerInput, setCustomerInput] = useState("");
  const [activeCustomerTask, setActiveCustomerTask] = useState<CustomerTaskId>("price");
  const [completedCustomerTasks, setCompletedCustomerTasks] = useState<CustomerTaskId[]>([]);
  const [customerOutcome, setCustomerOutcome] = useState<CustomerOutcome | null>(null);
  const [isCustomerPending, startCustomerTransition] = useTransition();

  const [adminMessages, setAdminMessages] = useState<ChatMessage[]>(seedAdminMessages);
  const [adminInput, setAdminInput] = useState("");
  const [adminStage, setAdminStage] = useState<AdminStage>("company");
  const [companyName, setCompanyName] = useState("");
  const [offerings, setOfferings] = useState<DraftOffering[]>([]);
  const [pendingProduct, setPendingProduct] = useState<DraftOffering | null>(null);
  const [importFileName, setImportFileName] = useState<string | null>(null);
  const [policyConfigured, setPolicyConfigured] = useState(false);

  const [toast, setToast] = useState("");
  const messageId = useRef(100);
  const customerComposerRef = useRef<HTMLInputElement>(null);
  const adminComposerRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const conversationRef = useRef<HTMLDivElement>(null);

  const messages = mode === "customer" ? customerMessages : adminMessages;

  useEffect(() => {
    conversationRef.current?.scrollTo({
      top: conversationRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, mode, adminStage, pendingProduct, importFileName]);

  const notify = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 2600);
  };

  const appendCustomer = (...next: Array<Omit<ChatMessage, "id">>) => {
    setCustomerMessages((current) => [
      ...current,
      ...next.map((message) => ({ ...message, id: messageId.current++ })),
    ]);
  };

  const appendAdmin = (...next: Array<Omit<ChatMessage, "id">>) => {
    setAdminMessages((current) => [
      ...current,
      ...next.map((message) => ({ ...message, id: messageId.current++ })),
    ]);
  };

  const runCustomerTask = (taskId: CustomerTaskId, questionOverride?: string) => {
    if (isCustomerPending) return;
    const task = getCustomerTask(taskId);
    const question = questionOverride?.trim() || task.question;
    setActiveCustomerTask(taskId);
    setCustomerOutcome(null);
    setCustomerInput("");

    const history = customerMessages.slice(-8).map(({ role, text }) => ({
      role: role === "customer" ? "customer" : "assistant",
      text,
    }));
    appendCustomer({ role: "customer", text: question });

    startCustomerTransition(async () => {
      try {
        const response = await fetch("/api/simulator/customer-turn", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: question, history }),
        });
        const payload = (await response.json()) as unknown;
        if (!response.ok || !isCustomerTurnResponse(payload)) {
          throw new Error("Yerel model güvenli bir yanıt planı üretmedi.");
        }

        appendCustomer({
          role: "assistant",
          text: payload.reply,
          outcome: payload.action,
        });
        setCustomerOutcome({
          action: payload.action,
          factIds: payload.factIds,
          latencyMs: payload.latencyMs,
        });
        if (customerTaskWasSatisfied(task, payload)) {
          setCompletedCustomerTasks((current) =>
            current.includes(taskId) ? current : [...current, taskId],
          );
        }
      } catch {
        // A failed safety check is deliberately not turned into a canned bot
        // reply. The chat must show the same thing the live runtime would do:
        // no customer-facing message was emitted for this turn.
        setCustomerOutcome({ action: "response-withheld", factIds: [] });
      }
    });
  };

  const submitCustomer = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = customerInput.trim();
    if (!value || isCustomerPending) return;
    runCustomerTask(activeCustomerTask, value);
  };

  const finishCompany = (rawName: string) => {
    const name = rawName.trim();
    if (!name) return;
    setCompanyName(name);
    setAdminInput("");
    appendAdmin(
      { role: "admin", text: name },
      {
        role: "assistant",
        text:
          "Harika, " +
          name +
          " için bir taslak açtım. Şimdi ilk ürününü, hizmetini veya varyantını yazalım.",
      },
    );
    setAdminStage("products");
    notify("Şirket bilgisi taslağa eklendi.");
  };

  const proposeProduct = (rawName: string) => {
    const name = rawName.trim();
    if (!name) return;
    const offering: DraftOffering = {
      id: "manual-" + messageId.current,
      name,
      price: "Fiyat henüz girilmedi",
      source: "Sohbet",
    };
    setPendingProduct(offering);
    setAdminInput("");
    appendAdmin(
      { role: "admin", text: name },
      {
        role: "assistant",
        text: "Bunu önce önizleme olarak hazırladım. Taslağa eklememi açıkça onaylar mısın?",
      },
    );
  };

  const confirmProduct = () => {
    if (!pendingProduct) return;
    setOfferings((current) => [...current, pendingProduct]);
    appendAdmin(
      { role: "admin", text: "Taslağa ekle" },
      {
        role: "assistant",
        text:
          "“" +
          pendingProduct.name +
          "” taslağa eklendi. Başka bir ürün, hizmet veya varyant var mı?",
      },
    );
    setPendingProduct(null);
    notify("Ürün taslağa eklendi; liste hâlâ açık.");
  };

  const startImport = () => {
    fileInputRef.current?.click();
  };

  const receiveImport = (file?: File) => {
    if (!file) return;
    setImportFileName(file.name);
    appendAdmin(
      { role: "admin", text: "Belge seçildi: " + file.name },
      {
        role: "assistant",
        text: "Dosyada 2 aday ürün kaydı buldum. Bunlar henüz taslağa veya müşteri görünümüne yazılmadı; önce önizleyip kabul etmelisin.",
      },
    );
  };

  const applyImportCandidates = () => {
    if (!importFileName) return;
    const candidates: DraftOffering[] = [
      {
        id: "import-" + messageId.current,
        name: "AX-500 Kasnak",
        price: "2.100 TL + KDV",
        source: "Excel/PDF",
      },
      {
        id: "import-" + (messageId.current + 1),
        name: "AX-600 Kasnak",
        price: "2.650 TL + KDV",
        source: "Excel/PDF",
      },
    ];
    setOfferings((current) => [...current, ...candidates]);
    appendAdmin(
      { role: "admin", text: "2 aday kaydı taslağa uygula" },
      {
        role: "assistant",
        text: "İki aday ürün taslağa eklendi. İstersen bir ürün daha ekleyebilir, istersen listeyi açıkça tamamlayabilirsin.",
      },
    );
    setImportFileName(null);
    notify("Aday kayıtlar taslağa eklendi; müşteri görünürlüğü kapalı.");
  };

  const cancelImport = () => {
    setImportFileName(null);
    appendAdmin(
      { role: "admin", text: "İçe aktarmayı iptal et" },
      {
        role: "assistant",
        text: "Tamam, belgeden hiçbir kayıt taslağa eklenmedi. Ürün listesinden devam edebiliriz.",
      },
    );
  };

  const finishProductCollection = () => {
    if (offerings.length === 0) {
      notify("Önce en az bir ürün veya hizmet eklemelisin.");
      adminComposerRef.current?.focus();
      return;
    }
    setAdminStage("rule");
    appendAdmin(
      { role: "admin", text: "Liste tamam" },
      {
        role: "assistant",
        text: "Ürün listesi kapatıldı. Şimdi müşteriye yalnızca onaylı bilgileri söyleme ve bilinmeyeni insana aktarma kuralını seçelim.",
      },
    );
  };

  const configurePolicy = () => {
    setPolicyConfigured(true);
    setAdminStage("review");
    appendAdmin(
      { role: "admin", text: "Yalnızca onaylı bilgiyle yanıtla" },
      {
        role: "assistant",
        text: "Kural taslağa eklendi: onaylı fact yoksa cevap uydurmak yerine insan aktarımı yapılacak. Şimdi müşteri görünümünde test edebilirsin.",
      },
    );
    notify("Bilgi kuralı taslağa eklendi.");
  };

  const submitAdmin = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = adminInput.trim();
    if (!value) return;

    if (adminStage === "company") {
      finishCompany(value);
      return;
    }

    if (adminStage === "products") {
      if (pendingProduct || importFileName) {
        notify("Önce açık önizlemeyi kabul et veya iptal et.");
        return;
      }
      proposeProduct(value);
      return;
    }

    appendAdmin(
      { role: "admin", text: value },
      {
        role: "assistant",
        text: "Bu serbest notu taslağa otomatik yazmıyorum. Görünen seçeneklerden güvenli olarak devam edelim.",
      },
    );
    setAdminInput("");
  };

  const resetSimulation = () => {
    setCustomerMessages(seedCustomerMessages);
    setCustomerInput("");
    setActiveCustomerTask("price");
    setCompletedCustomerTasks([]);
    setCustomerOutcome(null);

    setAdminMessages(seedAdminMessages);
    setAdminInput("");
    setAdminStage("company");
    setCompanyName("");
    setOfferings([]);
    setPendingProduct(null);
    setImportFileName(null);
    setPolicyConfigured(false);

    messageId.current = 100;
    notify("Yerel simülasyon başlangıç durumuna döndü.");
  };

  const goToAdminTask = (stage: AdminStage) => {
    if (stage === "company") {
      setAdminStage(stage);
      return;
    }
    if (stage === "products" && !companyName) {
      notify("Önce şirket adını tamamla.");
      return;
    }
    if (stage === "rule" && offerings.length === 0) {
      notify("Bilgi kuralından önce bir ürün veya hizmet ekle.");
      return;
    }
    if (stage === "review" && !policyConfigured) {
      notify("Önizlemeden önce bilgi kuralını seç.");
      return;
    }
    setAdminStage(stage);
  };

  const customerTaskCount = completedCustomerTasks.length;
  const adminStageIndex = adminSteps.findIndex((step) => step.id === adminStage);
  const adminReady = Boolean(companyName && offerings.length > 0 && policyConfigured);

  return (
    <main className={styles.page}>
      <header className={styles.topbar}>
        <div className={styles.brand}>
          <span className={styles.brandMark} aria-hidden="true">
            <MessageCircle size={22} />
          </span>
          <span>
            <strong>Ashira AI</strong>
            <small>WhatsApp akış simülatörü</small>
          </span>
        </div>

        <div className={styles.modeSwitch} aria-label="Simülasyon modu">
          <button
            type="button"
            aria-pressed={mode === "customer"}
            className={classNames(mode === "customer" && styles.modeSelected)}
            onClick={() => setMode("customer")}
          >
            <UserRound size={17} />
            Müşteri
          </button>
          <button
            type="button"
            aria-pressed={mode === "admin"}
            className={classNames(mode === "admin" && styles.modeSelected)}
            onClick={() => setMode("admin")}
          >
            <ClipboardCheck size={17} />
            Admin
          </button>
        </div>

        <div className={styles.topActions}>
          <span className={styles.localStatus}>
            <ShieldCheck size={16} />
            Yerel simülasyon
          </span>
          <button type="button" className={styles.resetButton} onClick={resetSimulation}>
            <RefreshCcw size={16} />
            Sıfırla
          </button>
        </div>
      </header>

      <section className={styles.workspace}>
        <aside className={styles.leftRail}>
          {mode === "customer" ? (
            <>
              <div className={styles.railHeading}>
                <h1>Müşteri görevleri</h1>
                <p>Bir senaryo seç veya aşağıdan kendi sorunu yaz.</p>
              </div>

              <div className={styles.taskList}>
                {customerTasks.map((task, index) => {
                  const complete = completedCustomerTasks.includes(task.id);
                  const selected = activeCustomerTask === task.id;
                  return (
                    <button
                      type="button"
                      key={task.id}
                      className={classNames(styles.taskButton, selected && styles.taskSelected)}
                      onClick={() => runCustomerTask(task.id)}
                    >
                      <span
                        className={classNames(styles.taskMarker, complete && styles.taskComplete)}
                      >
                        {complete ? <Check size={15} /> : index + 1}
                      </span>
                      <span>
                        <strong>{task.title}</strong>
                        <small>{task.subtitle}</small>
                      </span>
                      <ChevronRight size={17} />
                    </button>
                  );
                })}
              </div>

              <div className={styles.railHint}>
                <HelpCircle size={18} />
                <p>
                  Amaç model kalitesini değil, yanıtın yalnızca onaylı bilgiyle mi kurulduğunu
                  görmektir.
                </p>
              </div>
            </>
          ) : (
            <>
              <div className={styles.railHeading}>
                <h1>Kurulum görevleri</h1>
                <p>Ham JSON yok; her adım sohbet, preview ve açık kabul ile ilerler.</p>
              </div>

              <div className={styles.setupProgress}>
                <span>{Math.min(adminStageIndex + 1, 4)} / 4</span>
                <i style={{ width: String(((adminStageIndex + 1) / 4) * 100) + "%" }} />
              </div>

              <div className={styles.taskList}>
                {adminSteps.map((step, index) => {
                  const complete = index < adminStageIndex;
                  const selected = step.id === adminStage;
                  return (
                    <button
                      type="button"
                      key={step.id}
                      className={classNames(styles.taskButton, selected && styles.taskSelected)}
                      onClick={() => goToAdminTask(step.id)}
                    >
                      <span
                        className={classNames(styles.taskMarker, complete && styles.taskComplete)}
                      >
                        {complete ? <Check size={15} /> : index + 1}
                      </span>
                      <span>
                        <strong>{step.title}</strong>
                        <small>{step.detail}</small>
                      </span>
                      <ChevronRight size={17} />
                    </button>
                  );
                })}
              </div>

              <div className={styles.railHint}>
                <ShieldCheck size={18} />
                <p>
                  Açık ürün koleksiyonu kapanmadan bot uzak bir kurulum aşamasına kendiliğinden
                  geçmez.
                </p>
              </div>
            </>
          )}
        </aside>

        <section className={styles.chatColumn} aria-label="WhatsApp simülasyonu">
          <div className={styles.chatIntro}>
            <div>
              <span>{mode === "customer" ? "MÜŞTERİ GÖRÜNÜMÜ" : "YÖNETİCİ GÖRÜNÜMÜ"}</span>
              <h2>
                {mode === "customer"
                  ? "Yerel LLM ile doğal konuşmayı dene"
                  : "Şirketini konuşarak yapılandır"}
              </h2>
            </div>
            <span className={styles.simulationLabel}>CANLI SİMÜLASYON</span>
          </div>

          <section className={styles.chatPanel}>
            <header className={styles.chatHeader}>
              <ArrowLeft size={20} />
              <span className={styles.chatAvatar}>
                {mode === "customer" ? <Building2 size={19} /> : <Bot size={20} />}
              </span>
              <span className={styles.chatIdentity}>
                <strong>{mode === "customer" ? "Atlas Metal" : "Ashira Kurulum Asistanı"}</strong>
                <small>
                  <i />
                  {mode === "customer" ? "yerel LLM modu" : "çevrimiçi"}
                </small>
              </span>
              <MoreVertical size={20} className={styles.moreIcon} />
            </header>

            <div className={styles.conversation} ref={conversationRef}>
              <span className={styles.today}>BUGÜN</span>
              {messages.map((message) => (
                <article
                  className={classNames(
                    styles.messageRow,
                    message.role === "assistant" ? styles.fromAssistant : styles.fromUser,
                  )}
                  key={message.id}
                >
                  <div className={styles.bubble}>
                    <p>{message.text}</p>
                    {message.meta && (
                      <span
                        className={classNames(
                          styles.messageMeta,
                          message.outcome !== "answer" && styles.handoffMeta,
                        )}
                      >
                        {message.outcome === "answer" ? (
                          <BadgeCheck size={13} />
                        ) : (
                          <HandHeart size={13} />
                        )}
                        {message.meta}
                      </span>
                    )}
                    <span className={styles.messageTime}>
                      şimdi {message.role !== "assistant" && <CheckCheck size={14} />}
                    </span>
                  </div>
                </article>
              ))}

              {mode === "customer" && isCustomerPending && (
                <article className={classNames(styles.messageRow, styles.fromAssistant)}>
                  <div className={styles.thinkingBubble} aria-live="polite">
                    <span aria-hidden="true">
                      <i />
                      <i />
                      <i />
                    </span>
                    Yerel model düşünüyor…
                  </div>
                </article>
              )}

              {mode === "customer" &&
                !isCustomerPending &&
                customerOutcome?.action === "response-withheld" && (
                  <article className={classNames(styles.messageRow, styles.fromAssistant)}>
                    <div className={styles.withheldNotice} role="alert">
                      <CircleAlert size={17} aria-hidden="true" />
                      <span>
                        <strong>Yanıt gösterilemedi</strong>
                        Güvenlik kontrolü bu yanıtı durdurdu. Mesajı yeniden gönderebilirsin.
                      </span>
                    </div>
                  </article>
                )}

              {mode === "customer" ? (
                <div className={styles.quickActions} aria-label="Müşteri görevleri">
                  {customerTasks.map((task) => (
                    <button
                      type="button"
                      key={task.id}
                      onClick={() => runCustomerTask(task.id)}
                      disabled={isCustomerPending}
                    >
                      {task.id === "price" && <BadgeCheck size={15} />}
                      {task.id === "delivery" && <PackagePlus size={15} />}
                      {task.id === "unknown" && <HandHeart size={15} />}
                      {task.title}
                    </button>
                  ))}
                </div>
              ) : (
                <>
                  {adminStage === "products" && pendingProduct && (
                    <section className={styles.proposalCard} aria-label="Ürün önizlemesi">
                      <div className={styles.proposalTopline}>
                        <span>
                          <Sparkles size={15} />
                          Önizleme
                        </span>
                        <button
                          type="button"
                          onClick={() => setPendingProduct(null)}
                          aria-label="Önizlemeyi iptal et"
                        >
                          <X size={16} />
                        </button>
                      </div>
                      <strong>{pendingProduct.name}</strong>
                      <p>
                        Fiziksel ürün · {pendingProduct.price}. Taslağa yalnızca açık kabulden sonra
                        eklenir.
                      </p>
                      <div className={styles.proposalActions}>
                        <button
                          type="button"
                          className={styles.primaryAction}
                          onClick={confirmProduct}
                        >
                          <Check size={16} />
                          Taslağa ekle
                        </button>
                        <button
                          type="button"
                          className={styles.secondaryAction}
                          onClick={() => setPendingProduct(null)}
                        >
                          Düzenle
                        </button>
                      </div>
                    </section>
                  )}

                  {adminStage === "products" && importFileName && (
                    <section className={styles.proposalCard} aria-label="Belgeden aday kayıtlar">
                      <div className={styles.proposalTopline}>
                        <span>
                          <FileSpreadsheet size={15} />
                          Belgeden adaylar
                        </span>
                        <button
                          type="button"
                          onClick={cancelImport}
                          aria-label="İçe aktarmayı iptal et"
                        >
                          <X size={16} />
                        </button>
                      </div>
                      <strong>{importFileName}</strong>
                      <p>
                        AX-500 Kasnak ve AX-600 Kasnak bulundu. Bu kayıtlar henüz müşteri
                        görünürlüğüne açık değil.
                      </p>
                      <div className={styles.proposalActions}>
                        <button
                          type="button"
                          className={styles.primaryAction}
                          onClick={applyImportCandidates}
                        >
                          <Check size={16} />2 kaydı taslağa ekle
                        </button>
                        <button
                          type="button"
                          className={styles.secondaryAction}
                          onClick={cancelImport}
                        >
                          İptal
                        </button>
                      </div>
                    </section>
                  )}

                  {adminStage === "products" && !pendingProduct && !importFileName && (
                    <div className={styles.quickActions} aria-label="Ürün koleksiyonu seçenekleri">
                      <button type="button" onClick={() => adminComposerRef.current?.focus()}>
                        <Plus size={15} />
                        Bir tane daha ekle
                      </button>
                      <button type="button" onClick={startImport}>
                        <Upload size={15} />
                        Excel / PDF yükle
                      </button>
                      <button type="button" onClick={finishProductCollection}>
                        <Check size={15} />
                        Liste tamam
                      </button>
                    </div>
                  )}

                  {adminStage === "rule" && (
                    <div className={styles.ruleCard}>
                      <span className={styles.ruleIcon}>
                        <ShieldCheck size={19} />
                      </span>
                      <div>
                        <strong>Bilinmeyen bilgiye ne yapalım?</strong>
                        <p>
                          Müşteriye yalnızca kanıtı olan fact’ler söylenecek; diğer durumda konuşma
                          bir insana aktarılacak.
                        </p>
                        <button
                          type="button"
                          className={styles.primaryAction}
                          onClick={configurePolicy}
                        >
                          <Check size={16} />
                          Bu kuralı taslağa ekle
                        </button>
                      </div>
                    </div>
                  )}

                  {adminStage === "review" && (
                    <div className={styles.reviewCard}>
                      <span className={styles.ruleIcon}>
                        <ClipboardCheck size={19} />
                      </span>
                      <div>
                        <strong>Taslak test için hazır</strong>
                        <p>
                          Şirket, {offerings.length} teklif ve güvenli bilgi kuralı yerel taslakta
                          duruyor. Müşteri görünümünde fiyat, teslimat ve bilinmeyen bilgi
                          senaryolarını dene.
                        </p>
                        <button
                          type="button"
                          className={styles.primaryAction}
                          onClick={() => setMode("customer")}
                        >
                          <MessageCircle size={16} />
                          Müşteri modunda dene
                        </button>
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>

            <form
              className={styles.composer}
              onSubmit={mode === "customer" ? submitCustomer : submitAdmin}
            >
              {mode === "admin" && (
                <>
                  <input
                    ref={fileInputRef}
                    type="file"
                    className={styles.hiddenInput}
                    accept=".xlsx,.xls,.csv,.pdf"
                    onChange={(event) => receiveImport(event.target.files?.[0])}
                    aria-label="Excel veya PDF seç"
                  />
                  <button
                    type="button"
                    className={styles.attachButton}
                    onClick={startImport}
                    aria-label="Belge ekle"
                  >
                    <Paperclip size={20} />
                  </button>
                </>
              )}
              <input
                ref={mode === "customer" ? customerComposerRef : adminComposerRef}
                value={mode === "customer" ? customerInput : adminInput}
                onChange={(event) =>
                  mode === "customer"
                    ? setCustomerInput(event.target.value)
                    : setAdminInput(event.target.value)
                }
                disabled={mode === "customer" && isCustomerPending}
                placeholder={
                  mode === "customer"
                    ? "Mesaj yaz…"
                    : adminStage === "company"
                      ? "Şirketinin adını yaz…"
                      : adminStage === "products"
                        ? "Ürün veya hizmet adını yaz…"
                        : "Buradan serbest not değil, görünür seçimi kullan…"
                }
                aria-label={mode === "customer" ? "Müşteri mesajı" : "Yönetici mesajı"}
              />
              <button
                type="submit"
                className={styles.sendButton}
                aria-label="Mesaj gönder"
                disabled={mode === "customer" && isCustomerPending}
              >
                <SendHorizontal size={18} />
              </button>
            </form>
          </section>
        </section>

        <aside className={styles.rightRail}>
          {mode === "customer" ? (
            <>
              <div className={styles.rightHeading}>
                <h2>Durum</h2>
                <span>
                  {customerTaskCount} / {customerTasks.length}
                </span>
              </div>

              <div className={styles.stateBlock}>
                <span className={styles.stateIcon}>
                  <ListChecks size={18} />
                </span>
                <div>
                  <small>AKTİF GÖREV</small>
                  <strong>{getCustomerTask(activeCustomerTask).title}</strong>
                  <p>{getCustomerTask(activeCustomerTask).subtitle}</p>
                </div>
              </div>

              <div className={styles.stateBlock}>
                <span className={styles.stateIcon}>
                  {customerOutcome?.action === "handoff" ? (
                    <HandHeart size={18} />
                  ) : customerOutcome?.action === "response-withheld" ? (
                    <CircleAlert size={18} />
                  ) : (
                    <BadgeCheck size={18} />
                  )}
                </span>
                <div>
                  <small>SONUÇ</small>
                  <strong>
                    {customerOutcome
                      ? customerOutcome.action === "answer"
                        ? "Kanıtlı yanıt kuruldu"
                        : customerOutcome.action === "converse"
                          ? "Doğal konuşma kuruldu"
                          : customerOutcome.action === "clarify"
                            ? "Kapsam netleştiriliyor"
                            : customerOutcome.action === "handoff"
                              ? "Doğrulama gerekiyor"
                              : "Yanıt güvenlikte tutuldu"
                      : "Görev bekliyor"}
                  </strong>
                  <p>
                    {isCustomerPending
                      ? "Mesaj, onaylı fact kümesinde yorumlanıyor."
                      : customerOutcome?.factIds.length
                        ? "Fact ID: " + customerOutcome.factIds.join(", ")
                        : customerOutcome?.latencyMs
                          ? "Yerel Qwen · " + customerOutcome.latencyMs + " ms"
                          : customerOutcome
                            ? "Kanıt sözleşmesine uygun müşteri mesajı üretilmedi"
                            : "Senaryoyu çalıştır."}
                  </p>
                </div>
              </div>

              <div className={styles.statusNote}>
                <ShieldCheck size={17} />
                <p>
                  LLM önce anlam çerçevesini çıkarır, sonra JSON bilgi grafiğinden kanıt seçilir.
                  Doğal ifade ayrı kurulur; şirket iddiası yalnızca onaylı claim bloklarından gelir.
                </p>
              </div>
            </>
          ) : (
            <>
              <div className={styles.rightHeading}>
                <h2>Taslak</h2>
                <span>{adminReady ? "Hazır" : "Taslak"}</span>
              </div>

              <div className={styles.stateBlock}>
                <span className={styles.stateIcon}>
                  <Building2 size={18} />
                </span>
                <div>
                  <small>KURULUŞ</small>
                  <strong>{companyName || "Bekliyor"}</strong>
                  <p>{companyName ? "Şirket bilgisi eklendi" : "İlk görev tamamlanmadı"}</p>
                </div>
              </div>

              <div className={styles.stateBlock}>
                <span className={styles.stateIcon}>
                  <PackagePlus size={18} />
                </span>
                <div>
                  <small>TEKLİFLER</small>
                  <strong>{offerings.length} ürün / hizmet</strong>
                  <p>
                    {adminStage === "products"
                      ? "Koleksiyon açık"
                      : offerings.length
                        ? "Liste açıkça kapatıldı"
                        : "Henüz kayıt yok"}
                  </p>
                </div>
              </div>

              <div className={styles.stateBlock}>
                <span className={styles.stateIcon}>
                  <ShieldCheck size={18} />
                </span>
                <div>
                  <small>BİLGİ KURALI</small>
                  <strong>{policyConfigured ? "Kaynaklı yanıt" : "Bekliyor"}</strong>
                  <p>
                    {policyConfigured
                      ? "Bilinmeyen bilgi insana aktarılır"
                      : "Üçüncü görevde seçilir"}
                  </p>
                </div>
              </div>

              <div className={styles.statusNote}>
                <CircleAlert size={17} />
                <p>
                  Taslak yayınlanmadı. Buradaki hiçbir değişiklik WhatsApp’a veya gerçek bir şirkete
                  gönderilmez.
                </p>
              </div>
            </>
          )}
        </aside>
      </section>

      {toast && (
        <div className={styles.toast} role="status">
          <Check size={17} />
          {toast}
        </div>
      )}
    </main>
  );
}
