@echo off
node --check static\VoiceOrb.js
node --check static\settings.js
node --check static\companion_overlay.js
node --check static\music_workstation.js
python -c "import re,subprocess,os; p='index.html'; s=open(p,encoding='utf-8',errors='replace').read(); idx=0; ok=0\nwhile True:\n m=re.search(r'<script([^>]*)>',s[idx:])\n if not m: break\n attrs=m.group(1); start=idx+m.end(); end=s.find('</script>',start)\n if end==-1: break\n sc=s[start:end]; idx=end+9\n if 'src=' in attrs: ok+=1; continue\n scs=sc.strip()\n if not scs or scs.strip().startswith('{'): ok+=1; continue\n r=subprocess.run(['node','--check'],cwd=os.getcwd(),input=scs.encode('utf-8'),stdout=subprocess.PIPE,stderr=subprocess.PIPE)\n if r.returncode!=0: print('FAIL'); exit(1)\n ok+=1\nprint('OK',ok)\n"
