import json
from pymol import cmd
clsY=json.load(open('/tmp/eschColi_chr.trna64_Y_classes_code.json'))
clsZ=json.load(open('/tmp/trna84_Z_7nww_classes.json'))
cmd.reinitialize()
cmd.load('/home/ouyangzhuo/LLM-tRNAAlign/.runtime/pdb_vararm/9h0l.cif','long')
cmd.load('/home/ouyangzhuo/LLM-tRNAAlign/.runtime/pdb_vararm/7nww.cif','short')
cmd.remove('long and not chain Y'); cmd.remove('short and not chain z'); cmd.remove('solvent')
for n in ('long','short'):
    cmd.hide('everything',n); cmd.show('cartoon',n)
cmd.align('short and polymer','long and polymer')
cmd.set('cartoon_nucleic_acid_mode',2); cmd.set('cartoon_tube_radius',0.42)
cmd.set('cartoon_loop_radius',0.3); cmd.set('cartoon_ladder_radius',0.07)
cmd.bg_color('white'); cmd.set('ray_opaque_background',1); cmd.set('antialias',2)
def colour(name,cls,chain):
    cmd.color('grey75',name)
    for kind,col in (('v_stem','0x2E7D32'),('v_loop','0xC8924B'),('t_region','0x2563A6')):
        resi='+'.join(cls[kind])
        if resi:
            sel=f'{name}_{kind}'; cmd.select(sel,f'{name} and chain {chain} and resi {resi}'); cmd.color(col,sel)
colour('long',clsY,'Y'); colour('short',clsZ,'z')
cmd.orient('long or short'); cmd.turn('x',-20); cmd.turn('y',20); cmd.turn('z',10)
base=cmd.get_view()
for name,cls,chain,prefix in (('long',clsY,'Y','long'),('short',clsZ,'z','short')):
    cmd.disable('all'); cmd.enable(name); cmd.set_view(base)
    cmd.zoom(name,1.35); cmd.png(f'/tmp/{prefix}_overview.png',width=1000,height=1000,dpi=300,ray=1)
    cmd.set_view(base)
    vsel='+'.join(cls['v_stem']+cls['v_loop']+cls['t_region'])
    cmd.select('varm',f'{name} and chain {chain} and resi {vsel}')
    cmd.zoom('varm',3.2); cmd.png(f'/tmp/{prefix}_zoom.png',width=1000,height=1000,dpi=300,ray=1)
    print('done',name)
