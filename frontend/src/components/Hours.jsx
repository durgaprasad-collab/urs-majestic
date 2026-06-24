export default function Hours() {
  return (
    <section className="hours-section">
      <div className="container">
        <div className="section-heading">
          <h2>Hours & Contact</h2>
        </div>

        <div className="hours-card">
          <p className="hours-label">Opening Hours</p>
          <p className="hours-time">1:00 PM – 1:00 AM</p>
          <p className="hours-days">Monday – Sunday, all days</p>
        </div>

        <div className="contact-row">
          <a href="tel:+919150102001" className="btn btn-call">
            Call Us
          </a>
          <a
            href="https://wa.me/919150102001"
            className="btn btn-whatsapp"
            target="_blank"
            rel="noopener noreferrer"
          >
            WhatsApp
          </a>
        </div>
      </div>
    </section>
  )
}
