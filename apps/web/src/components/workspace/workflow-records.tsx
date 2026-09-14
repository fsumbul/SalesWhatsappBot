import { MotionDisclosure } from "../../lib/motion/components";
import styles from "./workflow.module.css";
export type WorkflowRecord = {
  id: string;
  title: string;
  subtitle: string;
  details: Record<string, string>;
  actions: { operation: string; label: string }[];
  file?: { request_id: string; file_id: string };
};
export default function RecordList({
  records,
  actions,
  page,
  hasMore,
  disabled,
  launch,
  changePage,
}: {
  records: WorkflowRecord[];
  actions: { operation: string; label: string }[];
  page: number;
  hasMore: boolean;
  disabled: boolean;
  launch: (operation: string, record?: string) => void;
  changePage: (page: number) => void;
}) {
  return (
    <section className={styles.records} aria-label="Kayıt listesi">
      <div>
        {actions.map((action) => (
          <button
            type="button"
            key={action.operation}
            disabled={disabled}
            onClick={() => launch(action.operation)}
          >
            {action.label}
          </button>
        ))}
      </div>
      <ul>
        {records.map((record) => (
          <li key={record.id}>
            <MotionDisclosure
              title={
                <>
                  <strong>{record.title}</strong>
                  <span>{record.subtitle}</span>
                </>
              }
            >
              <dl>
                {Object.entries(record.details).map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
              {record.file && (
                <a
                  href={`/api/platform/selection-requests/${encodeURIComponent(record.file.request_id)}/files/${encodeURIComponent(record.file.file_id)}`}
                  download
                >
                  Dosyayı indir
                </a>
              )}
              <div>
                {record.actions.map((action) => (
                  <button
                    type="button"
                    key={action.operation}
                    disabled={disabled}
                    onClick={() => launch(action.operation, record.id)}
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            </MotionDisclosure>
          </li>
        ))}
      </ul>
      {(page > 1 || hasMore) && (
        <nav aria-label="Kayıt sayfaları">
          <button
            type="button"
            disabled={disabled || page === 1}
            onClick={() => changePage(page - 1)}
          >
            Önceki sayfa
          </button>
          <span>Sayfa {page}</span>
          <button
            type="button"
            disabled={disabled || !hasMore}
            onClick={() => changePage(page + 1)}
          >
            Sonraki sayfa
          </button>
        </nav>
      )}
    </section>
  );
}
