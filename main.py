from flask import Flask, request, jsonify, send_file
from pytube import YouTube

app = Flask(__name__)

@app.route('/info', methods=['POST'])
def info():
    url = request.json['url']
    yt = YouTube(url)
    formats = [{
        'quality': stream.resolution or 'audio only',
        'type': stream.mime_type.split('/')[-1],
        'itag': stream.itag
    } for stream in yt.streams.filter(progressive=True)]
    return jsonify({'formats': formats})

@app.route('/download', methods=['POST'])
def download():
    url = request.json['url']
    itag = request.json['itag']
    yt = YouTube(url)
    stream = yt.streams.get_by_itag(itag)
    file_path = stream.download()
    return send_file(file_path, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
