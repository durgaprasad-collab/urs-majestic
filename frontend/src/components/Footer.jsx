import { PHONE, WHATSAPP } from '../data/site'

export default function Footer() {
  const year = new Date().getFullYear()
  return (
    <>
      <footer className="site-footer">
        <div className="wrap footer-row">
          <a href="#top" className="brand">
            <img src="/img/logo-circle-180.jpg" alt="" width="46" height="46" />
            <span><b>URS MAJESTIC</b><small>100% Pure Vegetarian</small></span>
          </a>
          <span>
            French Village Food Court, 200 Feet Road, Pallavaram, Chennai · Prices exclusive of taxes · © {year} URS Majestic
          </span>
        </div>
      </footer>
      <nav className="mobile-bar" aria-label="Quick actions">
        <a className="btn btn-ghost" href={`tel:${PHONE}`}>Call</a>
        <a className="btn btn-wa" href={WHATSAPP} target="_blank" rel="noopener noreferrer">WhatsApp</a>
        <a className="btn btn-gold" href="#order">Order online</a>
      </nav>
    </>
  )
}
