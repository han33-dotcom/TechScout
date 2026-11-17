export function compareDecimal(a:string,b:string) {
  const parts=(value:string)=>{
    const match=/^([0-9]+)(?:\.([0-9]*))?(?:[eE]([+-]?[0-9]+))?$/.exec(value);
    if(!match) throw new Error('Invalid amount from backend');
    const digits=(match[1]+(match[2]??'')).replace(/^0+/,'');
    return {digits, order:BigInt(digits.length-(match[2]?.length??0))+BigInt(match[3]??'0')};
  };
  const x=parts(a),y=parts(b);
  if(!x.digits||!y.digits)return x.digits?1:y.digits?-1:0;
  if(x.order!==y.order)return x.order<y.order?-1:1;
  const length=Math.max(x.digits.length,y.digits.length),left=x.digits.padEnd(length,'0'),right=y.digits.padEnd(length,'0');
  return left<right?-1:left>right?1:0;
}
