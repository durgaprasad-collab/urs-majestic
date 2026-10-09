import Icon from './Icon'
import useOpenStatus from '../useOpenStatus'
import { PHONE, PHONE_DISPLAY, DIRECTIONS, MAP_EMBED } from '../data/site'

export default function Visit() {
  const open = useOpenStatus()
  return (
    <section className="section visit" id="visit">
      <div className="wrap">
        <div className="section-head">
          <div><span className="orn eyebrow">Visit us</span><h2>Find us in Pallavaram</h2></div>
          <p>Inside French Village Food Court on 200 Feet Road.</p>
        </div>
        <div className="visit-grid">
          <div className="visit-map">
            <iframe
              src={MAP_EMBED}
              title="URS Majestic on Google Maps"
              loading="lazy"
              allowFullScreen
              referrerPolicy="strict-origin-when-cross-origin"
            />
          </div>
          <div className="visit-card">
            <div className="visit-row">
              <Icon name="pin" size={22} />
              <span><b>French Village Food Court</b><span>Thiruthani Nagar, 200 Feet Road,<br />Pallavaram, Chennai, Tamil Nadu</span></span>
            </div>
            <div className="visit-row">
              <Icon name="clock" size={22} />
              <span><b>1:00 PM – 1:00 AM</b><span>Monday to Sunday · {open ? 'Open now' : 'Opens at 1 PM'}</span></span>
            </div>
            <div className="visit-row">
              <Icon name="phone" size={22} />
              <span><b>{PHONE_DISPLAY}</b><span>Call or WhatsApp for parcels and bulk orders</span></span>
            </div>
            <div className="visit-btns">
              <a className="btn btn-gold" href={DIRECTIONS} target="_blank" rel="noopener noreferrer">Get directions</a>
              <a className="btn btn-ghost" href={`tel:${PHONE}`}>Call us</a>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
