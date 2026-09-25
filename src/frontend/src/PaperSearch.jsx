import { useState, useCallback } from "react";

const ARXIV_ABS_URL = (id) => `https://arxiv.org/abs/${id}`;
const ARXIV_PDF_URL = (id) => `https://arxiv.org/pdf/${id}`;

function formatDate(datestamp) {
  const d = new Date(datestamp);
  if (Number.isNaN(d.getTime())) return datestamp;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function RankBadge({ label, rank }) {
  if (rank === undefined || rank === null) return null;
  return (
    <span className="inline-flex items-baseline gap-1 text-[13px] text-stone-500">
      <span>{label}</span>
      <span className="font-medium text-amber-800 tabular-nums">#{rank}</span>
    </span>
  );
}

function PaperEntry({ paper, expanded, onToggleExpand }) {
  const abstract = paper.abstract || "";
  const isLong = abstract.length > 260;
  const shown = expanded || !isLong ? abstract : abstract.slice(0, 260).trimEnd() + "…";

  return (
    <li className="py-6 first:pt-0">
      <div className="flex flex-col gap-1.5">
        <h3 className="font-serif text-[19px] leading-snug text-stone-900">
          <a
            href={ARXIV_ABS_URL(paper.id)}
            target="_blank"
            rel="noreferrer"
            className="hover:text-blue-900 decoration-1 underline-offset-4 hover:underline"
          >
            {paper.title}
          </a>
        </h3>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-stone-500">
          <span className="tabular-nums">{paper.id}</span>
          <span aria-hidden="true" className="text-stone-300">
            /
          </span>
          <span>{formatDate(paper.datestamp)}</span>
          <span aria-hidden="true" className="text-stone-300">
            /
          </span>
          <RankBadge label="dense" rank={paper.rankers?.dense} />
          <RankBadge label="sparse" rank={paper.rankers?.sparse} />
        </div>

        <p className="mt-1.5 text-[15px] leading-relaxed text-stone-700">
          {shown}
          {isLong && (
            <button
              type="button"
              onClick={onToggleExpand}
              className="ml-2 text-[13px] font-medium text-blue-900 hover:underline"
            >
              {expanded ? "Show less" : "Read more"}
            </button>
          )}
        </p>

        <div className="mt-1 flex gap-4 text-[13px]">
          <a
            href={ARXIV_ABS_URL(paper.id)}
            target="_blank"
            rel="noreferrer"
            className="text-blue-900 hover:underline"
          >
            View on arXiv
          </a>
          <a
            href={ARXIV_PDF_URL(paper.id)}
            target="_blank"
            rel="noreferrer"
            className="text-blue-900 hover:underline"
          >
            Download PDF
          </a>
        </div>
      </div>
    </li>
  );
}

export default function PaperSearch({ apiBaseUrl = "http://localhost:8000/api" }) {
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [papers, setPapers] = useState(null);
  const [status, setStatus] = useState("idle"); // idle | loading | error | done
  const [errorMessage, setErrorMessage] = useState("");
  const [expandedIds, setExpandedIds] = useState(() => new Set());

  const runSearch = useCallback(
    async (q) => {
      const trimmed = q.trim();
      if (!trimmed) return;

      setStatus("loading");
      setErrorMessage("");
      setSubmittedQuery(trimmed);

      try {
        const res = await fetch(`${apiBaseUrl}/search`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: trimmed }),
        });

        if (!res.ok) {
          throw new Error(`Request failed with status ${res.status}`);
        }

        const data = await res.json();
        const results = Array.isArray(data.papers) ? data.papers : [];
        results.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));

        setPapers(results);
        setExpandedIds(new Set());
        setStatus("done");
      } catch (err) {
        setErrorMessage(
          err instanceof Error ? err.message : "Something went wrong."
        );
        setStatus("error");
      }
    },
    [apiBaseUrl]
  );

  const handleSubmit = (e) => {
    e.preventDefault();
    runSearch(query);
  };

  const toggleExpand = (id) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="min-h-screen bg-stone-50">
      <div className="mx-auto max-w-2xl px-6 py-14">
        <header className="mb-8">
          <h1 className="font-serif text-3xl text-stone-900">
            ArxivER
          </h1>
          <p className="mt-2 text-[15px] text-stone-500">
            Search by meaning. Combines dense and sparse retrieval over harvested arXiv's papers.
          </p>
        </header>

        <form onSubmit={handleSubmit} className="flex gap-2">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. reinforcement learning for SLAM failure recovery"
            className="flex-1 rounded-md border border-stone-300 bg-white px-4 py-2.5 text-[15px] text-stone-900 placeholder:text-stone-400 focus:border-blue-900 focus:outline-none focus:ring-1 focus:ring-blue-900"
          />
          <button
            type="submit"
            disabled={status === "loading" || !query.trim()}
            className="rounded-md bg-blue-950 px-5 py-2.5 text-[15px] font-medium text-white hover:bg-blue-900 disabled:cursor-not-allowed disabled:bg-stone-300"
          >
            Search
          </button>
        </form>

        <div className="mt-10">
          {status === "idle" && (
            <p className="text-[15px] text-stone-400">
              Results will appear here once you search.
            </p>
          )}

          {status === "loading" && (
            <p className="text-[15px] text-stone-400">Searching…</p>
          )}

          {status === "error" && (
            <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-[14px] text-red-800">
              Couldn't complete that search: {errorMessage}. Check that the API is running at {apiBaseUrl} and try again.
            </div>
          )}

          {status === "done" && (
            <>
              <p className="mb-2 text-[13px] text-stone-500">
                {papers.length}{" "}
                {papers.length === 1 ? "result" : "results"} for “
                {submittedQuery}”
              </p>

              {papers.length === 0 ? (
                <p className="text-[15px] text-stone-500">
                  No papers matched. Try a broader or differently phrased query.
                </p>
              ) : (
                <ul className="divide-y divide-stone-200">
                  {papers.map((paper) => (
                    <PaperEntry
                      key={paper.id}
                      paper={paper}
                      expanded={expandedIds.has(paper.id)}
                      onToggleExpand={() => toggleExpand(paper.id)}
                    />
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
