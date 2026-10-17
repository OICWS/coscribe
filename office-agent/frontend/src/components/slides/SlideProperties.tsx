import { transitionLabel, type SlideInfo } from "../../lib/slides";
import { ChevronDownIcon, CloseIcon } from "../icons";

const field =
  "flex h-8 min-w-0 flex-1 items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--field-bg)] px-2.5 text-sm text-[var(--fg)]";

/** The current slide's transition and background. Shown, not yet changed here: that comes with
 * the editor, so the fields read as values rather than controls. */
export function SlideProperties({ slide, beside, onClose }: { slide: SlideInfo; beside: boolean; onClose: () => void }) {
  const background = slide.background?.toUpperCase() ?? null;
  return (
    <aside
      aria-label="Slide properties"
      className={`shrink-0 overflow-auto bg-[var(--bg)] ${beside ? "w-[280px] border-l" : "max-h-[45%] border-t"} border-[var(--border)]`}
    >
      <div className="flex h-10 items-center justify-end px-2">
        <button
          type="button"
          title="Hide the properties panel"
          aria-label="Hide the properties panel"
          className="flex h-7 w-7 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
          onClick={onClose}
        >
          <CloseIcon className="h-4 w-4" />
        </button>
      </div>
      <section className="border-b border-[var(--border)] px-3 pb-3">
        <h3 className="pb-2.5 text-[15px] font-semibold text-[var(--fg)]">Slide</h3>
        <Row label="Transition">
          <div className={field} title="Ask coscribe to change the transition">
            <span className="flex-1 truncate">{transitionLabel(slide.transition)}</span>
            <ChevronDownIcon className="h-4 w-4 text-[var(--muted)]" />
          </div>
        </Row>
      </section>
      <section className="px-3 py-3">
        <h3 className="pb-2.5 text-[15px] font-semibold text-[var(--fg)]">Appearance</h3>
        <Row label="Background">
          <div className={field} title="Ask coscribe to change the background">
            <span
              className="h-[18px] w-[18px] shrink-0 rounded border border-[var(--border)]"
              style={{ background: background ?? "transparent" }}
            />
            <span className="flex-1 truncate font-mono text-[13px]">{background ?? "Drawn on the slide"}</span>
          </div>
        </Row>
      </section>
    </aside>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-[84px] shrink-0 text-sm text-[var(--muted)]">{label}</span>
      {children}
    </div>
  );
}
