/**
 * MethodologyView — Phase 5, Task 5.1.
 *
 * Static page explaining how the system computes risk: what each
 * sub-score measures, how the propagator turns local sub-scores into
 * a composite, what the six reproducibility fields mean, and how to
 * read the delta view. Linked from the App-shell ModeToggle row.
 *
 * No data dependencies — the page is intentionally static so it
 * loads even when the backend is offline. The propagator
 * configuration (name + version) reflects the Phase 4 decision
 * (ADR 0006); if a second strategy ships post-launch, this page
 * updates to describe both.
 */

import "./MethodologyView.css";

export const MethodologyView = () => {
  return (
    <article data-testid="methodology-view" className="methodology">
      <header>
        <h1>How StreetSense computes risk</h1>
        <p className="lede">
          StreetSense forecasts <em>where and when</em> road conditions will challenge ADAS
          perception systems &mdash; before incidents occur. This page explains how each number on
          the map is computed and how to read the delta view.
        </p>
      </header>

      <section>
        <h2>The four sub-scores</h2>
        <p>
          Every road segment carries four independent sub-scores in
          <code>[0, 1]</code>. Higher means worse. They are first-class throughout the API &mdash;
          composite risk never collapses them into a single opaque number.
        </p>
        <dl>
          <dt>Lane marking quality</dt>
          <dd>
            Computed from street-level imagery by a perception model (ONNX Runtime, swappable).
            Aggregates per-image lane-detection confidence into a per-segment score.
          </dd>
          <dt>Glare exposure</dt>
          <dd>
            Pure-functional solar geometry. For a given segment azimuth and a given hour-of-day,
            computes whether the sun's position puts it in a forward-camera's optical axis.
            Symmetric around solar noon for east&ndash;west arteries.
          </dd>
          <dt>Junction complexity</dt>
          <dd>
            Derived from OSM topology: nearby intersections, lane count, and connector geometry.
            Captures the structural difficulty of the segment independent of weather or time.
          </dd>
          <dt>Historical correlation</dt>
          <dd>
            Spatial association with recorded incidents in the surrounding window. Higher where
            prior incidents cluster.
          </dd>
        </dl>
      </section>

      <section>
        <h2>From sub-scores to composite risk</h2>
        <p>
          Composite risk is a weighted local aggregate plus a network contribution from a graph
          diffusion. The propagator is algorithm-agnostic at its public API; the strategy chosen in
          Phase 4 is <code>pagerank-diffusion-0.1.0</code>
          (see ADR&nbsp;0006). Concretely:
        </p>
        <pre className="formula">composite_risk = local_contribution + propagation_uplift</pre>
        <p>
          The composite always decomposes into these two parts in API responses &mdash; you can ask
          "how much of this risk came from this segment's own sub-scores vs. its neighbors?" without
          re-querying.
        </p>
      </section>

      <section>
        <h2>The six reproducibility fields</h2>
        <p>
          Every persisted score row carries six provenance fields. No row is written unless all six
          are known &mdash; the schema enforces NOT NULL.
        </p>
        <ol>
          <li>
            <code>scoring_run_id</code> &mdash; UUID for the run.
          </li>
          <li>
            <code>scoring_run_timestamp</code> &mdash; UTC instant the run started.
          </li>
          <li>
            <code>perception_model_version</code> &mdash; semver or git SHA of the ONNX artifact.
          </li>
          <li>
            <code>osm_snapshot_date</code> &mdash; date of the OSM extract.
          </li>
          <li>
            <code>imagery_capture_window</code> &mdash; start/end of imagery considered.
          </li>
          <li>
            <code>propagation_algorithm_version</code> &mdash; semver of the native propagator.
          </li>
        </ol>
        <p>
          These ship on every segment detail and on every delta response (for both sides of the
          comparison).
        </p>
      </section>

      <section>
        <h2>Reading the delta view</h2>
        <p>
          The delta view compares two scoring runs and paints each segment by the change in
          composite risk:
        </p>
        <ul>
          <li>
            <strong className="up">Red</strong> &mdash; risk increased between Run A and Run B.
          </li>
          <li>
            <strong className="down">Green</strong> &mdash; risk decreased.
          </li>
          <li>
            <strong className="flat">Grey</strong> &mdash; no meaningful change (within the
            dead-zone that filters scoring epsilon).
          </li>
        </ul>
        <p>
          Line width is a magnitude proxy &mdash; bigger absolute change, thicker line. The sorted
          "largest changes" list surfaces the top 100; the histogram shows the city-wide
          distribution. Clicking a row opens the segment detail panel where the per-sub-score deltas
          explain <em>why</em> composite moved.
        </p>
      </section>

      <section className="caveats">
        <h2>What this is not</h2>
        <ul>
          <li>Not a real-time service &mdash; cadence is weekly.</li>
          <li>Not an alerting system &mdash; the UI is view-only by design.</li>
          <li>Not multi-tenant &mdash; the live instance is gated behind one shared credential.</li>
        </ul>
      </section>
    </article>
  );
};
