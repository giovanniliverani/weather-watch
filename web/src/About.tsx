import { fetchAttributions } from './api'
import type { Attribution } from './types'
import { useResource } from './useResource'

const attributionCache = new Map<string, Attribution[]>()

export default function About({ onBack }: { onBack: () => void }) {
  const credits = useResource('all', fetchAttributions, attributionCache)
  return (
    <main className="about">
      <button type="button" className="link" onClick={onBack}>
        Back to the map
      </button>
      <h1>About Extreme Weather Watch</h1>
      <p>
        A private map of recent hazard events from authoritative feeds. Each pin is one event, joined across feeds by a reversible
        identity pipeline. News, posts and photos are links to their original pages; nothing but the link is stored.
      </p>
      <h2>Data sources and credits</h2>
      {credits.state === 'error' ? <p className="error">Could not load the credits: {credits.error.message}</p> : null}
      {credits.state === 'ready' ? (
        <dl className="credits">
          {credits.data.map((credit) => (
            <div key={credit.id}>
              <dt>{credit.name}</dt>
              <dd>
                {credit.attribution}
                {credit.terms_url ? (
                  <>
                    {' '}
                    <a href={credit.terms_url} target="_blank" rel="noreferrer">
                      Terms
                    </a>
                  </>
                ) : null}
              </dd>
            </div>
          ))}
        </dl>
      ) : credits.state === 'error' ? null : (
        <p className="hint">Loading…</p>
      )}
    </main>
  )
}
