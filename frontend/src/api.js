// One place that knows how to talk to the two services.
//
// The user header is the stub authentication both services accept. When real
// authentication lands, this is the only file that changes on the client.

const USER_HEADER = 'X-Sentinel-User'

function currentUser() {
  return localStorage.getItem('sentinel.user') || 'admin'
}

async function request(path, options = {}) {
  let response
  try {
    response = await fetch(path, {
      ...options,
      signal: options.signal || AbortSignal.timeout(15000),
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        [USER_HEADER]: currentUser(),
        ...(options.headers || {}),
      },
    })
  } catch (error) {
    if (error.name === 'AbortError' && options.signal) throw error
    throw new Error(
      error.name === 'TimeoutError'
        ? 'The service is taking too long to respond. Please try again.'
        : 'Cannot reach the Sentinel service. Check the connection and try again.',
    )
  }
  if (!response.ok) {
    let detail
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : null
    } catch {
      /* A proxy may return an HTML error page. */
    }
    throw new Error(
      detail ||
        (response.status >= 500
          ? 'The Sentinel service is unavailable. Please try again shortly.'
          : `Request could not be completed (${response.status}). Please check your input.`),
    )
  }
  if (response.status === 204) return null
  if (!response.headers.get('content-type')?.includes('application/json')) {
    throw new Error('The Sentinel service is not connected to this preview.')
  }
  return response.json()
}

export const api = {
  // map
  cameras: () => request('/map/cameras'),
  coverage: () => request('/map/coverage'),

  // search
  searchRegistration: (body) =>
    request('/search/registration', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  searchDescription: (body) =>
    request('/search/description', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  routesForSearch: (id) => request(`/searches/${id}/routes`),
  route: (id) => request(`/routes/${id}`),

  // alerts
  alerts: (params = '') => request(`/alerts${params}`),
  decideAlert: (id, body) =>
    request(`/alerts/${id}/decide`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // violations
  violationTypes: () => request('/violations/types'),
  violations: (params = '') => request(`/violations${params}`),
  reviewViolation: (id, body) =>
    request(`/violations/${id}/review`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // traffic
  trafficState: (params = '') => request(`/traffic/state${params}`),
  trafficCounts: (params = '') => request(`/traffic/counts${params}`),
  congestion: () => request('/traffic/congestion'),

  // evidence
  evidence: (readId) => request(`/evidence/${readId}`),
  frames: (params) => request(`/frames${params}`),
  // Stored references already include the kind and camera/date/hour folders.
  mediaUrl: (ref) =>
    ref ? `/media/${ref.split('/').map(encodeURIComponent).join('/')}` : undefined,
  sightings: (params = '') => request(`/live_sightings${params}`),

  // watchlist
  watchlist: () => request('/watchlist'),
  addWatchlist: (body) => request('/watchlist', { method: 'POST', body: JSON.stringify(body) }),

  // admin, on the registry
  adapters: () => request('/adapters'),
  queueDepth: () => request('/queues/depth'),
  camerasNeedingSurvey: () => request('/cameras/needing-survey'),
  departments: () => request('/departments'),
  onboardCamera: (cameraId, body) =>
    request(`/cameras/${cameraId}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  surveyCamera: (cameraId, body) =>
    request(`/cameras/${cameraId}/profile`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
}
