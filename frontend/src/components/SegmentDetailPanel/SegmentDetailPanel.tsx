/**
 * SegmentDetailPanel — composes the radial chart, confidence dial,
 * and imagery thumbnail strip. Mounts always; visibility is driven by
 * the `selectedSegment` Redux slice.
 *
 * Reads `selectedSegment` from Redux. When `isPanelOpen` is true and
 * a segment id is set, fires `useSegmentDetail` against the current
 * scrubber time and renders the contents.
 */
import { useEffect, useState } from "react";
import { useSelector } from "react-redux";

import { useSegmentDetail } from "../../data/useSegmentDetail";
import type { ImageryReference } from "../../domain";
import type { RootState } from "../../state/store";
import { useAppDispatch } from "../../state/hooks";
import { closePanel } from "../../state/selectedSegment";
import { ConfidenceDial } from "../ConfidenceDial";
import { SubScoreChart } from "../SubScoreChart";

import "./SegmentDetailPanel.css";

function _scrubberDate(dayOfYear: number, hourOfDay: number): Date {
  const base = new Date(Date.UTC(2025, 0, 1, 0, 0, 0));
  base.setUTCDate(dayOfYear);
  base.setUTCHours(hourOfDay);
  return base;
}

export function SegmentDetailPanel() {
  const dispatch = useAppDispatch();
  const segmentId = useSelector((s: RootState) => s.selectedSegment.segmentId);
  const isOpen = useSelector((s: RootState) => s.selectedSegment.isPanelOpen);
  const scrubber = useSelector((s: RootState) => s.scrubber);

  const at = _scrubberDate(scrubber.dayOfYear, scrubber.hourOfDay);
  const { data, isLoading, isError } = useSegmentDetail(segmentId, at);

  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);

  // Close lightbox on Escape.
  useEffect(() => {
    if (!lightboxUrl) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLightboxUrl(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightboxUrl]);

  if (!isOpen || !segmentId) return null;

  return (
    <aside
      className="segment-detail-panel"
      data-testid="segment-detail-panel"
      role="dialog"
      aria-label="Segment detail"
    >
      <header className="segment-detail-panel__header">
        <span className="segment-detail-panel__id">{segmentId.slice(0, 8)}…</span>
        <button
          type="button"
          className="segment-detail-panel__close"
          onClick={() => dispatch(closePanel())}
          aria-label="Close segment detail"
          data-testid="segment-detail-close"
        >
          ✕
        </button>
      </header>

      {isLoading && (
        <div className="segment-detail-panel__loading" data-testid="segment-detail-loading">
          Loading…
        </div>
      )}
      {isError && (
        <div className="segment-detail-panel__error">Failed to load segment detail.</div>
      )}
      {data && (
        <>
          <div className="segment-detail-panel__chart">
            <SubScoreChart subScores={data.sub_scores} />
          </div>
          <div className="segment-detail-panel__confidence">
            <ConfidenceDial confidence={data.confidence} />
          </div>

          <div
            className="segment-detail-panel__imagery"
            data-testid="segment-detail-imagery-strip"
          >
            {(data.imagery ?? []).length === 0 && (
              <div className="segment-detail-panel__no-imagery">No imagery available</div>
            )}
            {(data.imagery ?? []).map((ref: ImageryReference, i: number) => (
              <button
                key={i}
                type="button"
                className="segment-detail-panel__thumb"
                onClick={() => setLightboxUrl(ref.url)}
                data-testid="segment-detail-thumbnail"
              >
                <img src={ref.url} alt={`Source imagery ${i + 1}`} loading="lazy" />
              </button>
            ))}
          </div>
        </>
      )}

      {lightboxUrl && (
        <div
          className="segment-detail-panel__lightbox"
          data-testid="segment-detail-lightbox"
          onClick={() => setLightboxUrl(null)}
          role="presentation"
        >
          <img src={lightboxUrl} alt="Full-resolution source imagery" />
        </div>
      )}
    </aside>
  );
}
