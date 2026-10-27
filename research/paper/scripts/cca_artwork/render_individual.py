from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
BASE=Path(__file__).resolve().parent
# Reuse structural parsing and fixed projection without running the earlier layout.
source=(BASE/'render_cca_v2.py').read_text(encoding='utf-8-sig').split('fig=plt.figure(')[0]
exec(compile(source,'structure_helpers','exec'))
from matplotlib import font_manager
font_manager.fontManager.addfont('C:/Windows/Fonts/times.ttf')
font_manager.fontManager.addfont('C:/Windows/Fonts/timesbd.ttf')
plt.rcParams.update({'font.family':'Times New Roman','font.serif':['Times New Roman']})
DEST=BASE/'individual_compact';DEST.mkdir(exist_ok=True)
closepts=p(points)[:,:2]
left,right=closepts[:,0].min(),closepts[:,0].max()
bottom,top=closepts[:,1].min(),closepts[:,1].max()
newlims=np.array([[left-5,right+5],[bottom-2,top+2]])
for k,c in zip(IDS,COLORS):
 r=models[k];nums=list(r)[-3:]
 fig=plt.figure(figsize=(4,4.6))
 small=fig.add_axes([.12,.735,.29,.245])
 main=fig.add_axes([.08,.035,.84,.70])
 trace(small,ref,GRAY,.8,.65,smooth=True)
 trace(small,r,c,.85,1,smooth=True)
 trace(small,r,c,1.6,1,nums,smooth=True)
 small.add_patch(Rectangle(mn-2,*(mx-mn+4),fill=False,edgecolor=GRAY,lw=.6,linestyle=(0,(3,3))))
 small.set_xlim(wct[0]-wspan/2,wct[0]+wspan/2);small.set_ylim(wct[1]-wspan/2,wct[1]+wspan/2)
 small.set_aspect('equal');small.axis('off')
 fig.text(.12,1.005,k,color=c,fontsize=13,weight='bold',ha='left',va='bottom')
 trace(main,ref,GRAY,.7,.28,[71,72,73,74])
 sticks(main,ref,[74,75,76],GRAY,1,.6)
 sticks(main,r,nums,c,1.65,1)
 main.set_xlim(*newlims[0]);main.set_ylim(*newlims[1]);main.set_aspect('equal');main.axis('off')
 for rr,nn,cc,side in [(r,nums,c,1),(ref,[74,75,76],GRAY,-1)]:
  ordered=sorted(nn,key=lambda n:center(rr,n)[1]);ys=np.array([center(rr,n)[1] for n in ordered])
  for t in range(1,len(ys)):ys[t]=max(ys[t],ys[t-1]+4.8)
  ys-=max(0,ys[-1]-(lims[1,1]-4))
  grouppts=p([a for n in nn for a in rr[n]['atoms'].values()])[:,:2]
  x=grouppts[:,0].max()+2.5 if side==1 else grouppts[:,0].min()-2.5
  for n,y in zip(ordered,ys):
   main.annotate(rr[n]['name'],xy=center(rr,n),xytext=(x,y),ha='left' if side==1 else 'right',va='center',fontsize=11,color=cc,arrowprops={'arrowstyle':'-','color':cc,'lw':.65,'shrinkA':3,'shrinkB':2})
 require_matplotlib_panel_alignment(fig,axes=[main],exclude_axes=[small],panel_ids=['terminal'],json_out=DEST/f'{k}.alignment.json',strict=True)
 fig.savefig(DEST/f'{k}.png',dpi=300,facecolor='white',bbox_inches='tight',pad_inches=.025)
 fig.savefig(DEST/f'{k}.pdf',facecolor='white',bbox_inches='tight',pad_inches=.025)
 fig.savefig(DEST/f'{k}.svg',facecolor='white',bbox_inches='tight',pad_inches=.025)
 fig.savefig(DEST/f'{k}.tiff',dpi=600,facecolor='white',bbox_inches='tight',pad_inches=.025)
 plt.close(fig)
(DEST/'notes.txt').write_text('Four individual figures. Small upper whole-structure inset; large terminal main panel. Same source coordinates, projection and main-panel scale for all four. Inset is intentionally smaller and excluded from comparable-panel alignment checks. Reference 71-73 shown as faint trace; 74-76 as sticks. Model physical terminal three residues as coloured sticks; upstream model residues omitted from close-up for clarity. Original fit selection undocumented; actual numbering retained. Cubic interpolation only changes upper backbone presentation, passing through original C3-prime coordinates. Gray=reference. No extra caption text in figures.',encoding='utf-8')
print('Four figures exported')

