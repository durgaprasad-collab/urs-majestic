import useOpenStatus from '../useOpenStatus'

export default function Header() {
  const open = useOpenStatus()
  return (
    <header className="site-header">
      <div className="wrap header-row">
        <a href="#top" className="brand" aria-label="URS Majestic — back to top">
          <img src="/img/logo-circle-180.jpg" alt="" width="46" height="46" />
          <span>
            <b>URS MAJESTIC</b>
            <small>Pure Veg · Pallavaram</small>
          </span>
        </a>
        <nav className="header-nav" aria-label="Sections">
          <a href="#signature">Signature</a>
          <a href="#combos">Combos</a>
          <a href="#menu">Menu</a>
          <a href="#visit">Visit</a>
        </nav>
        <span className={`open-pill${open ? '' : ' closed'}`}>
          <i aria-hidden="true" />
          {open ? 'Open now' : 'Closed'}
          <span className="open-pill-long">{open ? ' · till 1 AM' : ' · opens 1 PM'}</span>
        </span>
        <a href="#order" className="btn btn-gold btn-sm header-order">Order online</a>
      </div>
    </header>
  )
}
