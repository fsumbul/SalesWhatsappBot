/**
 * Customer-facing company configuration.
 *
 * The simulator treats a company as a typed knowledge graph:
 *
 *   claim = (subject entity, predicate, approved customer wording)
 *
 * "price" and "delivery" below are example predicates, not fields baked into
 * the runtime. Any company can add entities and predicates without changing
 * the dialogue engine.
 */

export type CustomerTurnAction = "answer" | "clarify" | "handoff" | "converse";

export type KnowledgeRequestKind = "knowledge" | "non_knowledge";
export type EvidenceState = "available" | "unavailable";
/**
 * The missing-information topology is part of the policy language. An
 * unspecified dimension can be clarified; an explicitly requested but
 * unconfigured dimension must be handed off rather than guessed.
 */
export type KnowledgeGap =
  | "none"
  | "subject_unspecified"
  | "predicate_unspecified"
  | "subject_and_predicate"
  | "unsupported_scope";
export type CustomerLanguageRegister = "informal" | "formal" | "neutral";
export type CustomerConversationIntent =
  | "engage"
  | "knowledge_request"
  | "close";

export interface ConversationJourneyPolicy {
  /** The organization whose context the conversation should naturally enter. */
  companyEntityId: string;
  /** Trusted assistant metadata; this is not a claim about the company. */
  assistantIdentity: string;
  /** Locale-owned, non-factual phrase that keeps the company as the topic. */
  companyTopicReference: string;
  /** Locale-owned open questions used when trusted journey metadata fully answers a turn. */
  discoveryQuestions: {
    informal: string;
    formal: string;
  };
  /** Locale-owned terminal response for the close stage. */
  closingMessage: string;
  /** One durable goal shared by every open-conversation turn. */
  objective: string;
}

export interface CustomerKnowledgeEntity {
  id: string;
  kind: string;
  displayName: string;
  /**
   * Approved default description for a broad request about this entity. This
   * keeps "what is this/company?" inside the same evidence graph instead of
   * turning every broad introduction into a missing-predicate clarification.
   */
  overviewClaimId?: string;
  /** Pattern for recognizing a concrete identifier of this entity kind. */
  referencePattern?: string;
  /**
   * Every phrase by which this entity may be referred to in the configured
   * locale. Deictic references such as "şirketiniz" belong here as data.
   */
  aliases: readonly string[];
  /**
   * Terms that would make a generated prose segment look like a company claim.
   * Deictic aliases can stay in aliases without weakening this guard.
   */
  claimReferenceTerms?: readonly string[];
}

export interface CustomerKnowledgePredicate {
  id: string;
  description: string;
  /** Default subject for predicates whose meaning is the current company context. */
  defaultSubjectId?: string;
  aliases: readonly string[];
  queryExamples?: readonly string[];
}

export interface CustomerVisibleClaim {
  id: string;
  subjectId: string;
  predicateId: string;
  /**
   * This is the only approved wording of the underlying customer-facing
   * proposition. The model receives the id; the server inserts this text.
   */
  customerText: string;
}

export interface ConversationPolicyRule {
  id: string;
  priority: number;
  when: {
    request: KnowledgeRequestKind;
    evidence: EvidenceState;
    gap: KnowledgeGap;
  };
  action: CustomerTurnAction;
  /**
   * A semantic goal for the local LLM, never a customer-visible canned reply.
   * An administrator can later edit this via the configuration flow.
   */
  realizationInstruction: string;
  /** Optional trusted operational wording for states that must be deterministic. */
  trustedResponse?: string;
}

export interface CustomerConversationPolicy {
  locale: string;
  defaultRegister: Exclude<CustomerLanguageRegister, "neutral">;
  /** Locale-owned evidence for an explicitly formal customer address. */
  formalAddressMarkers: readonly string[];
  /** Locale-owned evidence for an explicitly informal customer address. */
  informalAddressMarkers: readonly string[];
  /** Locale-owned markers that would make the assistant impersonate the company. */
  companyVoiceMarkers: readonly string[];
  style: readonly string[];
  /**
   * Terms that may not appear in model-written prose unless a fact block is
   * present. They are locale-specific policy data, not runtime heuristics.
   */
  ungroundedClaimReferenceTerms: readonly string[];
  naturalLanguageTemperature: number;
  maxReplyCharacters: number;
  maxRealizationAttempts: number;
  journey: ConversationJourneyPolicy;
  rules: readonly ConversationPolicyRule[];
}

export interface CustomerCompanyConfiguration {
  schemaVersion: "customer-company-config/1.0";
  entities: readonly CustomerKnowledgeEntity[];
  predicates: readonly CustomerKnowledgePredicate[];
  claims: readonly CustomerVisibleClaim[];
  conversation: CustomerConversationPolicy;
}

export interface CustomerConversationMessage {
  role: "customer" | "assistant";
  text: string;
}

export interface CustomerTurnResponse {
  action: CustomerTurnAction;
  factIds: string[];
  reply: string;
  latencyMs: number;
  engine: "local-llm";
}

/**
 * A small Atlas Metal instance of the universal document. The shape, rather
 * than these particular values, is the product contract.
 */
export const simulatorCompanyConfiguration = {
  schemaVersion: "customer-company-config/1.0",
  entities: [
    {
      id: "organization/atlas-metal",
      kind: "organization",
      displayName: "Atlas Metal",
      overviewClaimId: "claim/atlas-metal/overview",
      aliases: ["Atlas Metal", "şirketiniz", "şirket", "firmanız", "firma"],
      claimReferenceTerms: ["Atlas Metal"],
    },
    {
      id: "offering/ax-500",
      kind: "physical_product",
      displayName: "AX-500",
      referencePattern: "\\b[A-Z]{2}-\\d{3}\\b",
      aliases: ["AX-500", "AX 500", "AX500"],
      claimReferenceTerms: ["AX-500", "AX 500", "AX500"],
    },
  ],
  predicates: [
    {
      id: "organization/overview",
      description: "bir kuruluşun genel tanıtımı ve asistanın yardımcı olabildiği kapsam",
      defaultSubjectId: "organization/atlas-metal",
      aliases: [
        "genel tanıtım",
        "şirket tanıtımı",
        "neyle alakalı",
        "ne işe yarıyor",
        "amacı ne",
        "ne hakkında",
      ],
      queryExamples: ["bu şirket neyle ilgili?", "şirketi tanıtır mısınız?"],
    },
    {
      id: "commercial/list-price",
      description: "bir teklifin birim liste fiyatı",
      aliases: ["fiyat", "fiyatı", "fiyati", "ücret", "kaç TL", "ne kadar"],
      queryExamples: ["fiyatı ne kadar?", "kaç TL?"],
    },
    {
      id: "fulfilment/standard-lead-time",
      description: "bir teklifin standart üretim ve teslimat süresi",
      aliases: ["teslimat", "teslim", "ne zaman", "süre"],
      queryExamples: ["teslimat süresi nedir?", "ne zaman teslim edilir?"],
    },
  ],
  claims: [
    {
      id: "claim/atlas-metal/overview",
      subjectId: "organization/atlas-metal",
      predicateId: "organization/overview",
      customerText:
        "Atlas Metal, metal ürünleri sunan bir şirkettir. Bu asistan ürün, fiyat ve teslimat bilgileri konusunda yardımcı olur.",
    },
    {
      id: "claim/ax-500/list-price",
      subjectId: "offering/ax-500",
      predicateId: "commercial/list-price",
      customerText: "AX-500 birim liste fiyatı 2.100 TL + KDV'dir.",
    },
    {
      id: "claim/ax-500/standard-lead-time",
      subjectId: "offering/ax-500",
      predicateId: "fulfilment/standard-lead-time",
      customerText: "AX-500 için standart üretim ve teslimat süresi 7 iş günüdür.",
    },
  ],
  conversation: {
    locale: "tr-TR",
    defaultRegister: "formal",
    formalAddressMarkers: [
      "siz",
      "sizin",
      "size",
      "sizi",
      "misiniz",
      "mısınız",
      "musunuz",
      "müsünüz",
      "eder misiniz",
      "verir misiniz",
    ],
    informalAddressMarkers: [
      "sen",
      "senin",
      "sana",
      "seni",
      "misin",
      "mısın",
      "musun",
      "müsün",
      "istersin",
      "ister misin",
    ],
    companyVoiceMarkers: [
      "biz",
      "bizim",
      "şirketimiz",
      "firmamız",
      "ürünümüz",
      "hizmetimiz",
      "Atlas Metal olarak",
    ],
    style: ["doğal", "sıcak", "kısa", "müşterinin üslubuna yakın"],
    ungroundedClaimReferenceTerms: [
      "şirket",
      "şirketimiz",
      "firmamız",
      "biz",
      "bizim",
      "ürün",
      "ürünümüz",
      "hizmet",
      "hizmetimiz",
    ],
    naturalLanguageTemperature: 0.3,
    maxReplyCharacters: 360,
    maxRealizationAttempts: 3,
    journey: {
      companyEntityId: "organization/atlas-metal",
      assistantIdentity: "Atlas Metal'in yapay zekâ asistanıyım",
      companyTopicReference: "Atlas Metal hakkında",
      discoveryQuestions: {
        informal: "Atlas Metal hakkında ne öğrenmek istersin?",
        formal: "Atlas Metal hakkında ne öğrenmek istersiniz?",
      },
      closingMessage: "Görüşmek üzere, iyi günler.",
      objective:
        "Müşterinin son hamlesine önce doğrudan, sıcak ve kısa karşılık ver. Konuşma açıksa müşteriyi zorlamadan Atlas Metal bağlamına taşı, gerçek bilgi ihtiyacını keşfet ve yalnızca yapılandırılmış kanıtlarla ilerle. Mesajı aynen tekrar etme, satış baskısı kurma ve söylenmemiş ihtiyaç varsayma.",
    },
    rules: [
      {
        id: "grounded-claim",
        priority: 100,
        when: { request: "knowledge", evidence: "available", gap: "none" },
        action: "answer",
        realizationInstruction:
          "Kanıt bloğu müşterinin bilgi sorusunu tek başına yanıtlar. Prose alanında varlık, özellik veya değerleri yeniden söyleme; yeni soru açmadan yalnızca kısa bir doğal karşılık veya geçiş kur.",
      },
      {
        id: "missing-subject",
        priority: 90,
        when: { request: "knowledge", evidence: "unavailable", gap: "subject_unspecified" },
        action: "clarify",
        realizationInstruction:
          "İstenen bilgi boyutu anlaşılmış, ancak hangi nesne için olduğu belirsiz. Doğal biçimde yalnızca gerekli kapsamı sor.",
      },
      {
        id: "missing-information-dimension",
        priority: 80,
        when: {
          request: "knowledge",
          evidence: "unavailable",
          gap: "predicate_unspecified",
        },
        action: "clarify",
        realizationInstruction:
          "Konu anlaşılmış, ancak müşterinin hangi bilgi boyutunu istediği belirtilmemiş. Yeni konu veya örnek eklemeden doğal biçimde yalnızca gerekli kapsamı sor.",
      },
      {
        id: "unsupported-scope",
        priority: 70,
        when: { request: "knowledge", evidence: "unavailable", gap: "unsupported_scope" },
        action: "handoff",
        realizationInstruction:
          "Müşterinin açıkça istediği bilgi doğrulanmış değil. Doğal biçimde yalnızca doğrulama veya yönlendirme eylemini teklif et; konu, nesne veya özellik hakkında yeni şirket olgusu ekleme.",
        trustedResponse: "Bu bilgiyi netleştirmek için ilgili ekibe yönlendirebilirim.",
      },
      {
        id: "ambiguous-knowledge",
        priority: 60,
        when: { request: "knowledge", evidence: "unavailable", gap: "subject_and_predicate" },
        action: "clarify",
        realizationInstruction:
          "Bilgi talebi var ama hem konu hem bilgi boyutu belirsiz. Konuşmayı sürdürmek için müşterinin bağlamını doğal biçimde sor.",
      },
      {
        id: "open-conversation",
        priority: 10,
        when: { request: "non_knowledge", evidence: "unavailable", gap: "none" },
        action: "converse",
        realizationInstruction:
          "Mesaja doğal ve bağlama uygun karşılık ver. Şirket, ürün, hizmet veya ticari koşul hakkında yeni bir olgu ekleme.",
      },
    ],
  },
} as const satisfies CustomerCompanyConfiguration;

export function canonicalClaimIds(
  configuration: CustomerCompanyConfiguration,
  ids: readonly string[],
): string[] {
  const selected = new Set(ids);
  return configuration.claims.filter((claim) => selected.has(claim.id)).map((claim) => claim.id);
}

export function customerClaimText(
  configuration: CustomerCompanyConfiguration,
  ids: readonly string[],
): string[] {
  const selected = new Set(ids);
  return configuration.claims
    .filter((claim) => selected.has(claim.id))
    .map((claim) => claim.customerText);
}
