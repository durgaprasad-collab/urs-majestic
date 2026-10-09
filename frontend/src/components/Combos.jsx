import { priceOf } from '../data/site'

export default function Combos() {
  const express = ['Biryani Express — Veg', 'Biryani Express — Mushroom', 'Biryani Express — Paneer'].map(priceOf)
  return (
    <section className="section combos" id="combos">
      <div className="wrap">
        <div className="section-head">
          <div><span className="orn eyebrow">Best value</span><h2>Combos</h2></div>
          <p>A full meal on one plate, priced to keep coming back.</p>
        </div>
        <div className="combo-grid">
          <div className="combo combo-hot">
            <span className="badge">Best seller</span>
            <h3>Paneer Power Combo</h3>
            <p>Butter Naan, Butter Masala and Paneer 65.</p>
            <div className="combo-price"><b>₹{priceOf('Paneer Power Combo')}</b><span>per combo</span></div>
          </div>
          <div className="combo">
            <h3>Mini North Indian Meal</h3>
            <p>Our everyday North Indian plate.</p>
            <div className="combo-price"><b>₹{priceOf('Mini North Indian Meal')}</b><span>per combo</span></div>
          </div>
          <div className="combo">
            <h3>Biryani Express</h3>
            <p>Veg ₹{express[0]} · Mushroom ₹{express[1]} · Paneer ₹{express[2]}</p>
            <div className="combo-price"><b><small>from </small>₹{Math.min(...express)}</b><span>3 choices</span></div>
          </div>
          <div className="combo">
            <h3>Chinese Mega Box</h3>
            <p>The big Indo-Chinese box.</p>
            <div className="combo-price"><b>₹{priceOf('Chinese Mega Box')}</b><span>per box</span></div>
          </div>
        </div>
      </div>
    </section>
  )
}
