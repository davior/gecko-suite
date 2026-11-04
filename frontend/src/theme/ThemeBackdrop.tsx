/** The fixed layer `glass.css` expects behind the page, painted by the theme's variables. */
export default function ThemeBackdrop() {
  return (
    <div
      id="theme-bg"
      aria-hidden="true"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: -1,
        background: 'var(--theme-bg, transparent)',
        backgroundSize: 'var(--theme-bg-size, cover)',
        backgroundPosition: 'center',
        filter: 'var(--theme-bg-filter, none)',
      }}
    />
  )
}
