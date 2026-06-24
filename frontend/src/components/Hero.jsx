export default function Hero() {
  return (
    <section className="hero">
      {/* sr-only h1 keeps semantic/SEO structure; logo image is the visual heading */}
      <h1 className="sr-only">URS Majestic — Pure Vegetarian Restaurant, Pallavaram, Chennai</h1>
      <img
        src="/logo.jpeg"
        alt="URS Majestic — Pure Vegetarian"
        className="logo logo--hero"
        width="140"
        height="140"
      />
      <p className="hero-tagline">Freshly made. Every single day.</p>
      <p className="hero-location">
        French Village Food Court, Thiruthani Nagar,<br />
        200 Feet Road, Pallavaram, Chennai
      </p>
      <div className="hero-cta">
        <a
          href="https://www.swiggy.com/city/chennai/urs-majestic-pallavaram-rest1389673"
          className="btn btn-swiggy"
          aria-label="Order on Swiggy"
          target="_blank"
          rel="noopener noreferrer"
        >
          <img src="/swiggy.png" alt="" className="btn-favicon" aria-hidden="true" />
          Order on Swiggy
        </a>
        <a
          href="http://zoma.to/r/22783970"
          className="btn btn-zomato"
          aria-label="Order on Zomato"
          target="_blank"
          rel="noopener noreferrer"
        >
          <img src="/zomato.png" alt="" className="btn-favicon" aria-hidden="true" />
          Order on Zomato
        </a>
      </div>
      <a href="#menu" className="hero-scroll-hint">View menu ↓</a>
    </section>
  )
}
