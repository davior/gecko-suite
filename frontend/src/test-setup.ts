import '@testing-library/jest-dom/vitest'

// jsdom implements no layout, so it has no scrollIntoView — every real browser does.
// Stubbed rather than guarded at the call site: adding an optional-call for a
// universally supported API would be defensive code for a test-environment gap.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {}
}

// jsdom ships no IntersectionObserver, and the library grid uses one to page as you
// scroll. A no-op stub rather than a guard at the call site: the alternative is an
// optional-chained observer in production code to accommodate a test environment.
if (!('IntersectionObserver' in globalThis)) {
  class NoopIntersectionObserver implements IntersectionObserver {
    readonly root = null
    readonly rootMargin = ''
    readonly thresholds: ReadonlyArray<number> = []
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords(): IntersectionObserverEntry[] {
      return []
    }
  }
  globalThis.IntersectionObserver =
    NoopIntersectionObserver as unknown as typeof IntersectionObserver
}

// jsdom ships no matchMedia either, and the asset panel asks it whether there is room to
// dock beside the library or whether it has to cover the screen. The stub reports no
// match, so suites that do not care about it get the full-screen chrome; a suite that
// wants the docked chrome spies on this and says so out loud.
// A `'matchMedia' in window` check narrows `window` to `never` here — the DOM types
// say the method exists, and only the runtime disagrees.
if (typeof window.matchMedia !== 'function') {
  window.matchMedia = function matchMedia(query: string): MediaQueryList {
    return {
      media: query,
      matches: false,
      onchange: null,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    }
  }
}

// And no ResizeObserver, which the auto-growing description and summary boxes use to
// re-measure when the panel is dragged to a different width.
if (!('ResizeObserver' in globalThis)) {
  class NoopResizeObserver implements ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = NoopResizeObserver as unknown as typeof ResizeObserver
}
