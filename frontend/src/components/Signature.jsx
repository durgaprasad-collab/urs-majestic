import { priceOf } from '../data/site'

// Photos from our own kitchen. A dish only shows a name and price once it is
// confirmed against the menu; the rest carry a general caption.
const DISHES = [
  { img: 'mushroom-biryani', title: 'Mushroom Biryani', sub: 'Fragrant basmati, mushrooms, raita', menu: 'Mushroom Biryani / Pulav', badge: 'Most loved', big: true },
  { img: 'paneer-biryani', title: 'Paneer Biryani', sub: 'With raita', menu: 'Paneer Biryani / Pulav' },
  { img: 'gobi-manchurian', title: 'Indo-Chinese starters', sub: 'Tossed hot from the wok' },
  { img: 'fried-rice-chilli-gobi', title: 'Fried rice & starters', sub: 'Indo-Chinese, made to order' },
  { img: 'platter', title: 'Indo-Chinese platter', sub: 'Plated fresh for the table' },
]

export default function Signature() {
  return (
    <section className="section" id="signature">
      <div className="wrap">
        <div className="section-head">
          <div><span className="orn eyebrow">Signature plates</span><h2>Made in our kitchen</h2></div>
          <p>Real photos from our kitchen. Every dish is cooked to order and plated the way it reaches your table.</p>
        </div>
        <div className="dishes">
          {DISHES.map((d) => (
            <a key={d.img} className={`dish${d.big ? ' dish-big' : ''}`} href="#menu">
              {d.badge && <span className="badge">{d.badge}</span>}
              <img src={`/img/${d.img}.jpg`} alt={d.title} width="800" height="640" loading="lazy" />
              <span className="dish-cap">
                <span><b>{d.title}</b><small>{d.sub}</small></span>
                {d.menu && <span className="dish-price">₹{priceOf(d.menu)}</span>}
              </span>
            </a>
          ))}
        </div>
      </div>
    </section>
  )
}
