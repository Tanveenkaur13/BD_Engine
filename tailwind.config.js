module.exports = {
  content: ["./templates/**/*.html"],
  theme: { extend: {
    colors: {
      brand: { 50:'#FBF3F7', 100:'#F5E3EC', 200:'#E9C4D6', 500:'#9D3266',
               600:'#832850', 700:'#6B1D42', 800:'#551634', 900:'#3F1027' },
      violet2: { 50:'#F6F1FC', 100:'#EDE4F9', 200:'#DCC9F3', 500:'#8B45C7',
                 600:'#7A2FB8', 700:'#65239A' },
      ink: { DEFAULT:'#16181D', soft:'#4B5563', faint:'#8A929E' },
    },
    boxShadow: { card:'0 1px 2px rgba(16,18,29,.04), 0 1px 3px rgba(16,18,29,.06)' },
  }},
}
