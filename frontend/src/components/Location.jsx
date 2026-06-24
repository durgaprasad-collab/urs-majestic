export default function Location() {
  return (
    <section className="location-section">
      <div className="container">
        <div className="section-heading">
          <h2>Find Us</h2>
        </div>
        <div className="map-wrap">
          <iframe
            src="https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d3888.310354222551!2d80.17099897373143!3d12.951981815310795!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x3a525f2fc0404461%3A0x88b26c8f4fbd6c6!2sURS%20MAJESTIC!5e0!3m2!1sen!2sin!4v1782284251147!5m2!1sen!2sin"
            width="100%"
            height="400"
            style={{ border: 0 }}
            allowFullScreen
            loading="lazy"
            referrerPolicy="strict-origin-when-cross-origin"
            title="URS Majestic on Google Maps"
          />
        </div>
        <p className="address-text">
          French Village Food Court, Thiruthani Nagar,<br />
          200 Feet Road, Pallavaram, Chennai, Tamil Nadu
        </p>
        <a
          href="https://www.google.com/maps/dir/?api=1&destination=12.951982,80.170999"
          className="btn btn-directions"
          target="_blank"
          rel="noopener noreferrer"
        >
          Get Directions
        </a>
      </div>
    </section>
  )
}
