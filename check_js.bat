@echo off
setlocal
set NODE="C:\Program Files\nodejs\node.exe"
rem node --check on all static/*.js
for %%f in (static\*.js) do %NODE% --check %%f
if errorlevel 1 exit /b 1
rem node --check on every inline <script> block of index.html
python -c "import re,subprocess,os; s=open('index.html',encoding='utf-8',errors='replace').read(); idx=0; n=0\nwhile True:\n m=re.search(r'<script([^>]*)>',s[idx:])\n if not m: break\n a=m.group(1); st=idx+m.end(); en=s.find('</script>',st)\n if en<0: print('UNTERMINATED'); exit(1)\n sc=s[st:en]; idx=en+9\n if 'src=' in a: n+=1; continue\n scs=sc.strip()\n if not scs or scs.startswith('{') or '\"imports\"' in scs: n+=1; continue\n r=subprocess.run(['node','--check'],cwd=os.getcwd(),input=scs.encode('utf-8'),stdout=subprocess.PIPE,stderr=subprocess.PIPE)\n if r.returncode: print('FAIL inline block',n); exit(1)\n n+=1\nprint('INLINE_BLOCKS',n)\n"
if errorlevel 1 exit /b 1
echo ALL_CHECKS_PASS
