import Icon from './Icon'
import { PHONE, WHATSAPP, SWIGGY, ZOMATO, priceOf } from '../data/site'

export default function Hero() {
  return (
    <section className="hero" id="top">
      <div className="wrap hero-grid">
        <div className="hero-copy">
          <span className="eyebrow">Pure vegetarian · Pallavaram, Chennai</span>
          <h1>
            <span className="sr-only">URS Majestic — </span>
            Freshly made.<br /><em>Every single day.</em>
          </h1>
          <p className="lead">
            Biryanis, tandoor breads, rich paneer gravies and Indo-Chinese, cooked fresh in a 100% vegetarian
            kitchen. Dine in, take away, or order to your door.
          </p>
          <div className="chips">
            <span className="chip"><span className="veg" aria-hidden="true" />100% pure veg</span>
            <span className="chip">Open daily · 1 PM – 1 AM</span>
            <span className="chip">French Village Food Court</span>
          </div>
          <div className="cta" id="order">
            <a className="btn btn-swiggy" href={SWIGGY} target="_blank" rel="noopener noreferrer">
              <img src="/swiggy.png" alt="" aria-hidden="true" />Order on Swiggy
            </a>
            <a className="btn btn-zomato" href={ZOMATO} target="_blank" rel="noopener noreferrer">
              <img src="/zomato.png" alt="" aria-hidden="true" />Order on Zomato
            </a>
            <a className="btn btn-ghost" href={`tel:${PHONE}`}><Icon name="phone" size={17} />Call</a>
            <a className="btn btn-wa" href={WHATSAPP} target="_blank" rel="noopener noreferrer">WhatsApp</a>
          </div>
        </div>

        <div className="hero-art">
          <div className="hero-ring" aria-hidden="true" />
          <div className="hero-arch">
            <img
              src="/img/hero-mushroom-biryani.jpg"
              srcSet="/img/hero-mushroom-biryani-900.jpg 900w, /img/hero-mushroom-biryani.jpg 1600w"
              sizes="(max-width: 640px) 300px, 500px"
              alt="Mushroom biryani with raita and a crisp starter"
              width="1600"
              height="1000"
              fetchpriority="high"
            />
          </div>
          <div className="hero-stamp" aria-hidden="true"><div><span>100%</span>PURE<br />VEG</div></div>
          <div className="hero-tag">
            <span className="veg" aria-hidden="true" />
            <span><b>Mushroom Biryani</b><small>with raita</small></span>
            <span className="hero-tag-price">₹{priceOf('Mushroom Biryani / Pulav')}</span>
          </div>
        </div>
      </div>
    </section>
  )
}
