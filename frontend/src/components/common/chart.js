import {el, h, p} from '../../utils/dom.js';
import {value} from '../../utils/format.js';

export function plot(chart) {
  if (!chart.series.length) return el('section', {class:'plot'}, h(3,chart.title), p('无可绘制数据','empty'));
  const numbers = chart.series.map(item=>item.value);
  const min = Math.min(0,...numbers), max = Math.max(0,...numbers);
  const extent = max-min || 1;
  const zero = -min / extent * 100;
  return el('section',{class:'plot'},h(3,chart.title), el('ul',{class:'business-series'},
    chart.series.map(item=>{
      const ratio = (item.value - min) / extent * 100;
      const bar = el('i',{'aria-hidden':'true'});
      bar.style.left = `${Math.min(ratio,zero)}%`;
      bar.style.width = `${Math.abs(ratio-zero)}%`;
      return el('li',{}, el('span',{},item.label),el('span',{class:'bar-track','aria-hidden':'true'},bar),el('strong',{},value(item.value)));
    })));
}
